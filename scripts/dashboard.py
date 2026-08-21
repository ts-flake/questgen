"""questgen dashboard — the local UI for the whole pipeline.

Four tabs: Sources (preview a PDF, range-select / crop / mask pages, save to raw/ via
source_ops, always recorded in ops.json), Pipeline (run extract -> interim -> clean ->
tag -> build DB, with a guard before overwriting an edited bank), Bank (browse, filter,
edit, add, export .docx) and AI Gen (generate questions from the bank, then review).

Serves static/ and binds to 127.0.0.1 only; it reads and writes the content tree directly.
stdlib + PyMuPDF.  Run:  python3 scripts/dashboard.py  ->  http://127.0.0.1:8760
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import re
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fitz

import context
try:
    import export_docx          # needs python-docx; optional until export is used
    EXPORT_ERR = None
except Exception as _e:
    export_docx, EXPORT_ERR = None, f"{_e} — run: pip install python-docx pillow"
import bank
import interim_build
import llm_clean
import llm_gen
import llm_segment
import llm_tag
import mineru_extract
import source_ops

LOCK = threading.Lock()  # one save at a time
JOB_LOCK = threading.Lock()
JOB: dict = {"state": "idle", "name": None, "log": []}
CANCEL = threading.Event()
HOST = context.CONFIG.get("dashboard", {}).get("host", "127.0.0.1")
PORT = int(context.CONFIG.get("dashboard", {}).get("port", 8760))


# ---------------------------------------------------------------- helpers

def ctx_from_query(q: dict) -> context.Ctx:
    return context.Ctx(q.get("s", ["math"])[0], q.get("t", ["primary"])[0],
                       q.get("l", ["p6"])[0], q.get("src", [""])[0])


def safe_rel(f: str) -> str:
    f = f.replace("\\", "/")
    if ".." in f or f.startswith("/") or not (f.startswith("original/") or f.startswith("raw/")):
        raise ValueError(f"illegal path: {f}")
    return f


def list_files(ctx: context.Ctx) -> dict:
    out = {"original": [], "raw": []}
    for kind, d in (("original", ctx.original_dir), ("raw", ctx.raw_dir)):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() != ".pdf" or p.name.startswith("."):
                continue
            try:
                with fitz.open(p) as doc:
                    n = doc.page_count
            except Exception:
                n = None
            out[kind].append({"name": f"{kind}/{p.name}", "pages": n,
                              "size_mb": round(p.stat().st_size / 1e6, 1)})
    return out


def pipeline_status(ctx: context.Ctx) -> dict:
    files = []
    for p in sorted(ctx.raw_dir.glob("*.pdf")) if ctx.raw_dir.is_dir() else []:
        interim = None
        rp = ctx.interim_dir / f"{p.stem}.report.json"
        if rp.is_file():
            try:
                r = json.loads(rp.read_text(encoding="utf-8"))
                interim = {"questions": r["questions"], "flagged": r["flagged"],
                           "with_solution": r["with_solution"],
                           "warnings": r.get("warnings") or []}   # blocks dropped / 0 questions
            except Exception:
                interim = {}
        clean = None
        cp = ctx.interim_dir / f"{p.stem}.clean.log.json"
        if cp.is_file():
            try:
                clean = json.loads(cp.read_text(encoding="utf-8"))["summary"]
            except Exception:
                clean = {}
        tagged = None
        tp = ctx.interim_dir / f"{p.stem}.tag.log.json"
        if tp.is_file():
            try:
                tagged = json.loads(tp.read_text(encoding="utf-8"))["summary"]
            except Exception:
                tagged = {}
        live = interim_build.newest_stage(ctx, p.stem)[1]     # stage the bank actually serves
        files.append({"name": p.name, "size_mb": round(p.stat().st_size / 1e6, 1),
                      "extracted": mineru_extract.is_extracted(ctx, p.name),
                      "interim": interim, "clean": clean, "tagged": tagged, "live": live})
    return {"files": files, "token": bool(mineru_extract.load_token()),
            "llm": (llm_clean.endpoint() or {}).get("model"),
            "vlm": (llm_clean.vlm_endpoint() or {}).get("model"),
            "ctx": {"subject": ctx.subject, "stage": ctx.stage, "level": ctx.level, "source": ctx.source},
            "mock": bool(os.environ.get("MINERU_MOCK"))}


# ------------------------------------------------------------- entry writes (safety)
# Human edits are the only content in the tree that NO re-run can reproduce, so every
# write goes through these four guarantees:
#   1. ONE stage resolution (interim_build.newest_stage) for read and write — a save can
#      never land in a file the bank isn't reading.
#   2. The edit is MIRRORED into every stage file holding the qid (raw/clean/tagged), so
#      a later clean/tag re-run starts from edited input; _reseal_stage_order then puts
#      the mtimes back so the live stage stays live (an in-place rewrite must never flip
#      the bank onto a stale later/earlier stage — that is the "bank 倒退" failure).
#   3. Optimistic concurrency: the client sends the revision it loaded; a mismatch aborts
#      loudly instead of silently overwriting a newer save.
#   4. Every write journals its before/after pair (backups/edit_journal.jsonl) so a lost
#      edit is recoverable — see /api/edit_journal + /api/restore_entry.

# CONTENT fields only: flags/tags move on their own (verify toggle, a tag re-run) without
# the question itself changing, and those must not look like a conflicting edit.
REV_FIELDS = ("stem", "parts", "options", "answer", "solution", "imgs", "kind")


def entry_rev(row: dict) -> str:
    """Short content hash of an entry — the optimistic-concurrency token."""
    payload = {k: row.get(k) for k in REV_FIELDS}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha1(blob).hexdigest()[:12]


def _read_jsonl(path: Path, required: bool = True) -> list[dict]:
    """Whole-file, verified read (interim_build.read_jsonl) — a streaming read of an
    iCloud-evicted file silently yields nothing, and these rows get written straight back."""
    return interim_build.read_jsonl(Path(path), required=required)


def _reseal_stage_order(ctx: context.Ctx, stem: str, live: str) -> None:
    """After rewriting stage files in place, restore the mtime ranking so `live` is still
    the stage newest_stage picks: stages up to `live` get increasing mtimes, stages above
    it stay strictly older (they are stale derivatives)."""
    have = interim_build.existing_stages(ctx, stem)
    names = [s for s, _ in have]
    live_i = names.index(live) if live in names else len(names) - 1
    t = time.time() - 60
    for k, (_s, p) in enumerate(have):
        ts = t + k if k <= live_i else t + live_i - 1 - (k - live_i)
        try:
            os.utime(p, (ts, ts))
        except OSError:
            pass


def _journal_path(ctx: context.Ctx) -> Path:
    return _backups_dir(ctx) / "edit_journal.jsonl"


JOURNAL_KEEP = 200


def journal_edit(ctx: context.Ctx, kind: str, qid: str, stem: str,
                 before: dict | None, after: dict | None) -> None:
    """Append one before/after record to the per-source edit journal (kept to the last
    JOURNAL_KEEP entries). Cheap insurance against a clobbered or mis-saved edit."""
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "qid": qid,
           "file_stem": stem, "before": before, "after": after}
    p = _journal_path(ctx)
    try:
        rows = _read_jsonl(p) if p.is_file() else []
        _write_jsonl(p, (rows + [rec])[-JOURNAL_KEEP:])
    except Exception:
        pass                                   # journalling must never block a save


def edit_journal(ctx: context.Ctx, qid: str = "", limit: int = 40) -> dict:
    p = _journal_path(ctx)
    rows = _read_jsonl(p) if p.is_file() else []
    if qid:
        rows = [r for r in rows if r.get("qid") == qid]
    out = []
    for r in reversed(rows[-400:]):
        a = r.get("after") or {}
        out.append({"ts": r["ts"], "kind": r["kind"], "qid": r["qid"],
                    "file_stem": r.get("file_stem", ""),
                    "stem": (a.get("stem") or "")[:120],
                    "has_after": bool(r.get("after"))})
        if len(out) >= limit:
            break
    return {"journal": out}


def _apply_edit_fields(r: dict, ed: dict) -> None:
    """The field-level human edit, applied to ONE row (used for every stage file, each of
    which keeps its own tags/flags). Text goes through the deterministic normalizer;
    parts are re-nested; option/part labels are canonicalised."""
    ib = interim_build
    raw_stem = ed.get("stem", "")
    tm = ib.TOTAL_MARK.search(raw_stem)
    r.setdefault("meta", {})["marks"] = int(tm.group(1)) if tm else None
    r["stem"] = ib.normalize_text(raw_stem)
    sol = ed.get("solution", "")
    r["solution"] = ib.normalize_text(sol) if sol.strip() else None
    opts = {k: ib.normalize_text(v) for k, v in (ed.get("options") or {}).items() if v.strip()}
    r["options"] = opts or None
    av = (ed.get("answer") or "").strip()
    r["answer"] = {"value": av, "kind": "human"} if av else None
    aa = (ed.get("answer_area") or "").strip()
    r["answer_area"] = aa or None
    # The editor now sends the per-part fields, and finalize_parts preserves them, so an
    # emptied box actually clears (a carry-across would silently restore the old value).
    r["parts"] = ib.finalize_parts([p for p in (ed.get("parts") or []) if p.get("text", "").strip()])
    if "imgs" in ed:                              # persist uploads / deletions
        r["imgs"] = [a for a in ed["imgs"] if isinstance(a, dict) and a.get("path")]
    r["kind"] = "mcq" if r["options"] else "question"
    ib.canon_entry(r)                             # "(1)" options (answer follows), "(a)" parts
    ib.apply_answer_area(r)                       # keep the schema invariant on human edits
    ib.apply_part_answers(r)                      # parts <-> entry summary stay consistent
    r["meta"]["schema"] = ib.SCHEMA_VERSION
    typ = (ed.get("type") or "").strip()
    topics, diff = ed.get("topic"), (ed.get("difficulty") or "").strip()
    if typ or topics is not None or diff:         # human-set topic / type / difficulty tags
        tags = r.get("tags") or {"topic": [], "type": "", "difficulty": "medium"}
        if typ:
            cur_typ = tags.get("type")
            if typ not in llm_tag.problem_types() and typ != cur_typ:   # allow keeping a legacy type
                raise ValueError(f"bad type: {typ}")
            tags["type"] = typ
        if topics is not None:
            tags["topic"] = [t for t in topics if isinstance(t, str) and t.strip()]
        if diff:
            tags["difficulty"] = diff
        r["tags"] = tags
    r.setdefault("flags", [])
    r["flags"] = [f for f in r["flags"] if f not in ("no_answer", "no_solution", "short_stem")]
    if "human_edited" not in r["flags"]:
        r["flags"].append("human_edited")
    r["meta"]["edited_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def save_edit(ctx: context.Ctx, body: dict) -> dict:
    """Human edit of one entry, written to every stage file that holds it (see the
    guarantees above). Returns the saved entry so the client can patch its bank copy
    instead of re-fetching the whole bank."""
    ib = interim_build
    stem = str(body["file_stem"])
    if "/" in stem or ".." in stem:
        raise ValueError("bad stem")
    qid = body["qid"]
    ed = body["entry"]
    live_path, live_stage = ib.newest_stage(ctx, stem)
    if live_path is None:
        raise ValueError(f"no interim file for {stem}")
    live_rows = _read_jsonl(live_path)
    cur = next((r for r in live_rows if r.get("qid") == qid), None)
    if cur is None:
        raise ValueError(f"qid {qid} not found in {live_path.name}")
    ib.canon_entry(cur)                           # compare against what the bank served
    rev = str(body.get("rev") or "")
    if rev and rev != entry_rev(cur):
        raise ValueError("该题在磁盘上的版本与本次编辑的起点不一致 (可能是流水线重跑或别处保存过) — "
                         "请关闭并重新打开编辑窗口, 以免覆盖更新的内容")
    before = json.loads(json.dumps(cur, ensure_ascii=False))
    written, saved = [], None
    for stage, p in ib.existing_stages(ctx, stem):       # raw → clean → tagged
        rows = live_rows if p == live_path else _read_jsonl(p)
        hit = next((r for r in rows if r.get("qid") == qid), None)
        if hit is None:
            continue
        _apply_edit_fields(hit, ed)
        _write_jsonl(p, rows)
        written.append(p.name)
        if p == live_path:
            saved = hit
    if not written:
        raise ValueError(f"qid {qid} not found for {stem}")
    _reseal_stage_order(ctx, stem, live_stage)
    journal_edit(ctx, "edit", qid, stem, before, saved)
    out = dict(saved or {}, file_stem=stem, stage=live_stage,
               cleaned=live_stage in ("clean", "tagged"))
    out["_rev"] = entry_rev(saved or {})
    return {"ok": True, "files": written, "stage": live_stage, "entry": out, "rev": out["_rev"]}


def add_entry(ctx: context.Ctx, body: dict) -> dict:
    """Create a brand-new, manually-authored question in this source's bank. It lives in a
    dedicated `manual.jsonl` stage file alongside the extracted stems, so it shows up in the
    Bank right away (and is validated like any other entry). Reuses the item-editor
    fields plus topic/difficulty tags."""
    ib = interim_build
    ed = body.get("entry") or {}
    if not (str(ed.get("stem", "")).strip() or ed.get("parts") or ed.get("options")):
        raise ValueError("题目不能为空 (至少填题干、子题或选项) / question is empty")
    ctx.interim_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.interim_dir / "manual.jsonl"
    rows = _read_jsonl(path) if path.is_file() else []
    used = {r.get("qid") for r in rows}
    n = 1
    while f"manual-{n:03d}" in used:              # unique manual-NNN (extracted qids never use this prefix)
        n += 1
    qid = f"manual-{n:03d}"
    row = {"qid": qid, "meta": {"file": "manual"}}
    _apply_edit_fields(row, ed)                   # stem/parts/options/answer/solution/type/topic/difficulty/marks/flags
    row.setdefault("flags", [])
    if "manual" not in row["flags"]:
        row["flags"].append("manual")
    ib.canon_entry(row)
    rows.append(row)
    _write_jsonl(path, rows)
    _BANK_CACHE.pop(str(ctx.source_dir), None)    # invalidate so a reload also picks it up
    out = dict(row, file_stem="manual", stage="raw", cleaned=False)
    out["_rev"] = entry_rev(row)
    return {"ok": True, "qid": qid, "entry": out}


def delete_entry(ctx: context.Ctx, body: dict) -> dict:
    """Remove one entry (by qid) from every interim stage file that holds it, so the
    deletion sticks regardless of which stage the bank reads. Journalled (restorable)."""
    ib = interim_build
    stem = str(body["file_stem"])
    if "/" in stem or ".." in stem:
        raise ValueError("bad stem")
    qid = body["qid"]
    _live, live_stage = ib.newest_stage(ctx, stem)
    removed, before = [], None
    for _stage, p in ib.existing_stages(ctx, stem):
        rows = _read_jsonl(p)
        keep = [r for r in rows if r.get("qid") != qid]
        if len(keep) == len(rows):
            continue
        if before is None:
            before = next(r for r in rows if r.get("qid") == qid)
        _write_jsonl(p, keep, allow_empty=True)   # deleting the last entry is legitimate
        removed.append(p.name)
    if not removed:
        raise ValueError(f"qid {qid} not found for {stem}")
    _reseal_stage_order(ctx, stem, live_stage or "raw")
    journal_edit(ctx, "delete", qid, stem, before, None)
    return {"ok": True, "removed": removed}


def restore_entry(ctx: context.Ctx, body: dict) -> dict:
    """Put a journalled version of an entry back into every stage file that holds the qid
    (recovery path for an edit lost to a pipeline re-run). ts selects the record; the
    newest record for the qid is used when ts is omitted."""
    ib = interim_build
    qid = str(body.get("qid") or "")
    ts = str(body.get("ts") or "")
    which = str(body.get("which") or "after")
    p = _journal_path(ctx)
    rows = _read_jsonl(p) if p.is_file() else []
    which = which if which in ("before", "after") else "after"
    cand = [r for r in rows if r.get("qid") == qid and (not ts or r.get("ts") == ts)]
    if not cand:
        raise ValueError("日志中无该题记录")
    # without an explicit ts, "restore" means the newest version that HAS content: the
    # last record may be a delete (after=None), which is not a restorable snapshot.
    rec = next((r for r in reversed(cand) if r.get(which)), cand[-1])
    snap = rec.get(which)
    if not snap:
        raise ValueError("该记录无可恢复内容")
    stem = rec.get("file_stem") or str(snap.get("meta", {}).get("file", "")).split("/")[-1]
    stem = stem[:-4] if stem.endswith(".pdf") else stem
    _live, live_stage = ib.newest_stage(ctx, stem)
    if not live_stage:
        raise ValueError(f"no interim file for {stem}")
    written = []
    for _stage, path in ib.existing_stages(ctx, stem):
        rows_p = _read_jsonl(path)
        idx = next((i for i, r in enumerate(rows_p) if r.get("qid") == qid), None)
        row = json.loads(json.dumps(snap, ensure_ascii=False))
        for k in ("file_stem", "stage", "cleaned", "_rev"):
            row.pop(k, None)
        if idx is None:
            rows_p.append(row)                    # deleted entry: put it back
        else:
            row["tags"] = rows_p[idx].get("tags", row.get("tags"))   # keep this stage's tags
            rows_p[idx] = row
        _write_jsonl(path, rows_p)
        written.append(path.name)
    _reseal_stage_order(ctx, stem, live_stage)
    journal_edit(ctx, "restore", qid, stem, None, snap)
    return {"ok": True, "files": written, "qid": qid, "ts": rec["ts"]}


CFG_SECTIONS = ("llm", "vlm", "gen")


def _sec_key_file(section: str, sec: dict) -> Path:
    return context.ENGINE_ROOT / (sec.get("key_file") or f"config/{section}_key.txt")


def _mineru_token_file() -> Path:
    rel = context.CONFIG.get("mineru", {}).get("token_file", "config/mineru_token.txt")
    return context.ENGINE_ROOT / rel


def read_config() -> dict:
    """Editable model config (llm/vlm/gen): base_url, model, temperature, thinking, and
    the effective API key (from api_key or the key_file); plus the MinerU token."""
    out = {}
    for s in CFG_SECTIONS:
        sec = context.CONFIG.get(s, {}) or {}
        kf = _sec_key_file(s, sec)
        key = sec.get("api_key") or (kf.read_text(encoding="utf-8").strip() if kf.is_file() else "")
        out[s] = {"base_url": sec.get("base_url", ""), "model": sec.get("model", ""),
                  "temperature": sec.get("temperature", 0.0),
                  "thinking": bool(sec.get("thinking", False)), "key": key}
    mtf = _mineru_token_file()
    mineru_token = mtf.read_text(encoding="utf-8").strip() if mtf.is_file() else ""
    return {"config": out, "problem_types": list(llm_tag.problem_types()),
            "mineru_token": mineru_token}


def _yaml_set_field(text: str, section: str, field: str, value) -> str:
    """Replace `<field>: ...` inside `section:`'s block, preserving the rest of the file
    (full-line comments included). Appends the field if absent. pyyaml round-trip would
    drop the config's comments, so we edit in place."""
    if isinstance(value, bool):
        sval = "true" if value else "false"
    elif isinstance(value, (int, float)):
        sval = repr(value)
    else:
        sval = '"' + str(value).replace('"', '\\"') + '"'
    lines = text.split("\n")
    in_sec = False
    sec_at = None
    for i, ln in enumerate(lines):
        if re.match(rf"^{re.escape(section)}:\s*(#.*)?$", ln):
            in_sec, sec_at = True, i
            continue
        if in_sec:
            if ln and not ln[0].isspace():           # next top-level key ends the section
                break
            m = re.match(rf"^(\s+){re.escape(field)}:\s*.*$", ln)
            if m:
                lines[i] = f"{m.group(1)}{field}: {sval}"
                return "\n".join(lines)
    if sec_at is not None:                            # field absent -> insert right after header
        lines.insert(sec_at + 1, f"  {field}: {sval}")
    return "\n".join(lines)


def _yaml_set_list(text: str, key: str, items: list) -> str:
    """Set/replace a top-level `key: [a, b, ...]` inline list, preserving the rest."""
    inline = "[" + ", ".join(str(i) for i in items) + "]"
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if re.match(rf"^{re.escape(key)}:", ln):
            cm = re.search(r"\s+#.*$", ln)                # keep the trailing inline comment
            lines[i] = f"{key}: {inline}{cm.group(0) if cm else ''}"
            return "\n".join(lines)
    return text.rstrip("\n") + f"\n{key}: {inline}\n"


def write_config(body: dict) -> dict:
    """Persist edits to config.yaml (model/base_url/temperature/thinking, problem_types) and
    API keys (to each section's key_file), then hot-reload. body = {section:{field:value}, ...}."""
    p = context.config_path()
    text = p.read_text(encoding="utf-8")
    if isinstance(body.get("problem_types"), list):
        types = [re.sub(r"[^a-z0-9_]", "", str(t).strip().lower()) for t in body["problem_types"]]
        types = [t for t in dict.fromkeys(types) if t]     # dedupe, drop empties, keep order
        if types:
            text = _yaml_set_list(text, "problem_types", types)
    for s in CFG_SECTIONS:
        d = body.get(s)
        if not isinstance(d, dict):
            continue
        for f in ("base_url", "model"):
            if f in d:
                text = _yaml_set_field(text, s, f, str(d[f]).strip())
        if "temperature" in d:
            text = _yaml_set_field(text, s, "temperature", float(d["temperature"]))
        if "thinking" in d:
            text = _yaml_set_field(text, s, "thinking", bool(d["thinking"]))
        if str(d.get("key", "")).strip():             # keys live in the key_file, not the yaml; empty = no change
            sec = context.CONFIG.get(s, {}) or {}
            kf = _sec_key_file(s, sec)
            kf.parent.mkdir(parents=True, exist_ok=True)
            kf.write_text(str(d["key"]).strip() + "\n", encoding="utf-8")
    if str(body.get("mineru_token", "")).strip():     # MinerU cloud token (own file, gitignored)
        mtf = _mineru_token_file()
        mtf.parent.mkdir(parents=True, exist_ok=True)
        mtf.write_text(str(body["mineru_token"]).strip() + "\n", encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="questgen_cfg_"))
    try:
        tf = tmp / "config.yaml"
        tf.write_text(text, encoding="utf-8")
        shutil.copy2(tf, p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    llm_clean.reload_cfg()                            # refresh CFG/VCFG/GCFG for endpoints
    return {"ok": True}


# ------------------------------------------------------------ M5 AI generation
def _gen_pending_path(ctx: context.Ctx) -> Path:
    return ctx.level_dir / "_generated" / "pending.jsonl"   # _-prefixed: discover skips it


def _read_pending(ctx: context.Ctx) -> list[dict]:
    p = _gen_pending_path(ctx)
    return _read_jsonl(p, required=False) if p.is_file() else []


def _write_jsonl(path: Path, rows: list[dict], allow_empty: bool = False) -> None:
    """Atomic-ish rewrite (temp + copy, for the fuse mount). Refuses to replace a file
    that has content with an EMPTY list unless asked to: every caller here reads rows and
    writes them straight back, so an empty write is almost always a failed read (see
    interim_build.read_jsonl and the iCloud note there), not a real deletion."""
    if not rows and not allow_empty and path.is_file() and path.stat().st_size > 0:
        raise ValueError(f"refusing to blank {path.name} (0 entries from a non-empty file)")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="questgen_jl_"))
    try:
        tf = tmp / path.name
        with open(tf, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        shutil.copy2(tf, path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def generate_questions(ctx: context.Ctx, body: dict) -> dict:
    """Generate n questions from the bank corpus, append to the level's review queue.
    Persists the batch's reference (few-shot) examples so the UI can show what was fed."""
    model = str(body.get("model") or "gen")
    ep = {"gen": llm_clean.gen_endpoint, "llm": llm_clean.endpoint,
          "vlm": llm_clean.vlm_endpoint}.get(model, llm_clean.gen_endpoint)()
    if ep is None:
        raise ValueError(f"{model} 模型未配置 (config.yaml / key)")
    topics = body.get("topics")
    if not isinstance(topics, list):
        topics = [body.get("topic")] if body.get("topic") else []
    topics = [str(t).strip() for t in topics if str(t).strip()]
    # user-picked bank entries as explicit reference corpus (override topic sampling)
    refs = body.get("refs")
    picked = [llm_gen.example_of(r) for r in refs] if isinstance(refs, list) and refs else None
    if not topics and picked:                        # no topic chosen -> infer from the references
        topics = sorted({t for e in picked for t in (e.get("topic") or [])})
    if not topics:
        raise ValueError("请至少选择一个 topic (或勾选 bank 参考题)")
    difficulty = str(body.get("difficulty") or "")
    n = max(1, min(20, int(body.get("n") or 3)))
    k = max(0, min(20, int(body.get("k") or 5)))
    with_sol = bool(body.get("with_solutions", True))
    prompt = str(body.get("prompt") or "")
    objectives = [o for o in (body.get("objectives") or []) if isinstance(o, dict) and o.get("lo")]
    qtype = str(body.get("qtype") or "")
    if qtype and qtype not in llm_tag.problem_types():
        qtype = ""
    tnames = {}
    try:
        tax = llm_tag.load_taxonomy(ctx)
        tnames = {t: (tax.get(t) or {}).get("name_en", "") for t in topics}
    except Exception:
        pass
    entries, examples = llm_gen.generate(ctx, ep, topics, difficulty, n, with_sol, prompt, k, tnames,
                                         examples=picked, objectives=objectives, qtype=qtype)
    if not entries:
        raise ValueError("生成失败或返回空 (模型无响应 / 格式错误)")
    ts = entries[0]["meta"]["gen"]["ts"]
    (_gen_pending_path(ctx).parent / "refs").mkdir(parents=True, exist_ok=True)
    _write_jsonl(_gen_pending_path(ctx).parent / "refs" / f"{ts}.jsonl", examples)
    _write_jsonl(_gen_pending_path(ctx), _read_pending(ctx) + entries)
    return {"ok": True, "n": len(entries), "entries": entries, "examples": examples, "ts": ts}


def gen_refs(ctx: context.Ctx, ts: str) -> dict:
    """The few-shot reference examples fed to the model for a generation batch (by ts)."""
    if not ts or "/" in ts or ".." in ts:
        return {"examples": []}
    p = _gen_pending_path(ctx).parent / "refs" / f"{ts}.jsonl"
    return {"examples": _read_jsonl(p, required=False) if p.is_file() else []}


def gen_pending(ctx: context.Ctx) -> dict:
    return {"entries": _read_pending(ctx)}


def gen_reject(ctx: context.Ctx, body: dict) -> dict:
    qid = body.get("qid")
    rows = _read_pending(ctx)
    keep = [r for r in rows if r.get("qid") != qid]
    if len(keep) == len(rows):
        raise ValueError("qid not in queue")
    _write_jsonl(_gen_pending_path(ctx), keep, allow_empty=True)
    return {"ok": True}


def gen_accept(ctx: context.Ctx, body: dict) -> dict:
    """Promote a pending generated entry into the synthetic 'ai_generated' source so it
    joins the bank; remove it from the queue."""
    qid = body.get("qid")
    rows = _read_pending(ctx)
    hit = next((r for r in rows if r.get("qid") == qid), None)
    if hit is None:
        raise ValueError("qid not in queue")
    dest = ctx.level_dir / "ai_generated" / "interim" / "generated.jsonl"
    existing = _read_jsonl(dest) if dest.is_file() else []
    existing.append(hit)
    _write_jsonl(dest, existing)
    _write_jsonl(_gen_pending_path(ctx), [r for r in rows if r.get("qid") != qid], allow_empty=True)
    return {"ok": True, "source": "ai_generated"}


def toggle_verified(ctx: context.Ctx, body: dict) -> dict:
    """Toggle the 'verified' flag (human review confirmation). Mirrored into every stage
    file, like an edit, so the mark survives a stage flip. Returns the resulting state."""
    ib = interim_build
    stem = str(body["file_stem"])
    if "/" in stem or ".." in stem:
        raise ValueError("bad stem")
    qid = body["qid"]
    live_path, live_stage = ib.newest_stage(ctx, stem)
    if live_path is None:
        raise ValueError(f"no interim file for {stem}")
    live = next((r for r in _read_jsonl(live_path) if r.get("qid") == qid), None)
    if live is None:
        raise ValueError(f"qid {qid} not found in {live_path.name}")
    state = "verified" not in (live.get("flags") or [])       # decided by the LIVE row
    for _stage, p in ib.existing_stages(ctx, stem):
        rows = _read_jsonl(p)
        hit = next((r for r in rows if r.get("qid") == qid), None)
        if hit is None:
            continue
        fl = hit.setdefault("flags", [])
        if state and "verified" not in fl:
            fl.append("verified")
        elif not state and "verified" in fl:
            fl.remove("verified")
        _write_jsonl(p, rows)
    _reseal_stage_order(ctx, stem, live_stage)
    return {"ok": True, "verified": state}


def upload_img(ctx: context.Ctx, body: dict) -> dict:
    """Save an uploaded image (base64) into a source's extraction images/ dir.
    Replace: pass the existing `path` to overwrite. New: omit path, get a fresh one back."""
    stem = str(body["file_stem"])
    src = body.get("src", "q")
    if "/" in stem or ".." in stem or src not in ("q", "ans"):
        raise ValueError("bad args")
    d = ctx.extracted_dir / (stem if src == "q" else f"{stem}_ans")
    (d / "images").mkdir(parents=True, exist_ok=True)
    rel = body.get("path")
    if rel:
        if ".." in rel or not rel.startswith("images/"):
            raise ValueError("bad path")
    else:
        ext = (body.get("ext") or "jpg").lstrip(".").lower()
        ext = ext if ext in ("jpg", "jpeg", "png") else "jpg"
        rel = f"images/upload_{int(time.time() * 1000)}.{ext}"
    data = base64.b64decode(body["data"].split(",")[-1])
    (d / rel).write_bytes(data)
    return {"ok": True, "path": rel, "src": src}


_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def upload_source_file(body: dict) -> dict:
    """Upload a PDF (or an image, normalized to a 1-page PDF) into a content path's original/
    (or raw/), creating the subject/stage/level/source folders if they don't exist yet — the
    way a new source is started. Path parts are validated. One file per call."""
    parts = {k: str(body.get(k, "")).strip() for k in ("subject", "stage", "level", "source")}
    for k, v in parts.items():
        if not _NAME_OK.match(v):
            raise ValueError(f"非法 {k}: {v!r} (仅字母数字 . _ -, 不能以点开头)")
    into = str(body.get("into") or "original")
    if into not in ("original", "raw"):
        raise ValueError("into 必须是 original 或 raw")
    # filename may be non-ASCII (e.g. Chinese) — only the path parts above must be ASCII-safe.
    # Accept a PDF or a common image; take the basename, block traversal / reserved chars.
    name = str(body.get("name") or "").strip().replace("\\", "/").split("/")[-1]
    ext = Path(name).suffix.lower()
    if (name in (".", "..") or name.startswith(".") or (ext != ".pdf" and ext not in IMG_EXTS)
            or any(ord(c) < 32 for c in name) or any(c in name for c in '/:*?"<>|')):
        raise ValueError("仅支持 PDF 或图片 (jpg/jpeg/png/webp/gif/bmp/tiff)")
    raw = base64.b64decode(str(body.get("data", "")).split(",")[-1])
    if not raw:
        raise ValueError("空文件")
    if ext == ".pdf":
        if raw[:5] != b"%PDF-":
            raise ValueError("不是有效的 PDF (缺少 %PDF 头)")
        data = raw
    else:                                            # image → normalize to a 1-page PDF (PyMuPDF)
        tmpc = Path(tempfile.mkdtemp(prefix="questgen_img_"))
        try:
            src = tmpc / ("in" + ext)
            src.write_bytes(raw)
            with fitz.open(src) as imgdoc:
                if imgdoc.page_count < 1:
                    raise ValueError("无效的图片")
                data = imgdoc.convert_to_pdf()       # 1-page PDF sized to the image
        except ValueError:
            raise
        except Exception:
            raise ValueError("无效的图片文件")
        finally:
            shutil.rmtree(tmpc, ignore_errors=True)
        name = Path(name).stem + ".pdf"              # image is stored as a 1-page PDF
    ctx = context.Ctx(parts["subject"], parts["stage"], parts["level"], parts["source"])
    dest_dir = ctx.original_dir if into == "original" else ctx.raw_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    tmp = Path(tempfile.mkdtemp(prefix="questgen_up_"))
    try:
        (tmp / name).write_bytes(data)
        shutil.copy2(tmp / name, dest)               # local temp then copy (fuse-safe)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"ok": True, "path": f"{into}/{name}", "bytes": len(data),
            "source": {k: parts[k] for k in ("subject", "stage", "level", "source")}}


def ai_assist(body: dict) -> dict:
    """Edit-assist: apply a free-text instruction to a selected snippet from a question field
    and return ONLY the edited snippet (goes straight back into the editor). Uses the llm
    endpoint (config `llm:`)."""
    text = str(body.get("text", ""))
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("prompt 不能为空 / prompt is empty")
    ep = llm_clean.endpoint()
    if not ep and not os.environ.get("QUESTGEN_LLM_MOCK"):
        raise ValueError("未配置 LLM 端点 (设置 → llm) / no LLM endpoint configured")
    system = (
        "你是考试题目的编辑助手。用户从题目某字段中选中了一段文本, 并给出一条指令。"
        "严格按指令修改这段文本, **只输出修改后的文本本身** —— 不要解释、不要加代码围栏(```), "
        "不要添加多余的前后缀或标点。保持 LaTeX / mhchem 语法正确, 输出会直接放回编辑框。\n"
        "You are an editing assistant for exam questions. Apply the instruction to the selected "
        "text and output ONLY the edited text — no explanation, no code fences, nothing else.")
    user = f"指令 / INSTRUCTION:\n{prompt}\n\n选中文本 / SELECTED TEXT:\n{text}"
    out = llm_clean.chat_text(ep or {}, system, user)
    if out is None:
        raise ValueError("AI 无有效返回, 请重试 / no response, try again")
    return {"output": out}


def open_in_file_manager(ctx: context.Ctx, which: str, name: str = "") -> dict:
    """Reveal a source folder (original/ or raw/) or the worksheet outputs/ folder in the OS
    file manager, selecting `name` within it when given. Restricted to the project's own
    folders — only opens or reveals, never reads or returns file contents.

    `name` comes from the browser, so it must be a bare filename that resolves to a real file
    directly inside the folder; anything else (a path, a symlink out, a missing file) is
    rejected rather than handed to the file manager."""
    dirs = {"original": ctx.original_dir, "raw": ctx.raw_dir, "outputs": ctx.outputs_dir}
    if which not in dirs:
        raise ValueError("bad which")
    target = dirs[which].resolve()
    root = context.content_root().resolve()
    if not (target == root or str(target).startswith(str(root) + os.sep)
            or target == ctx.outputs_dir.resolve()):
        raise ValueError("path outside allowed folders")
    if not target.is_dir():
        if which == "outputs":
            target.mkdir(parents=True, exist_ok=True)   # created on first export; make it if empty
        else:
            raise ValueError(f"folder does not exist: {which}/")
    reveal = None
    if name:
        if name != Path(name).name:
            raise ValueError("bad file name")
        f = (target / name).resolve()
        if f.parent != target or not f.is_file():
            raise ValueError("file not in that folder")
        reveal = f
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(reveal)] if reveal else ["open", str(target)],
                       check=False)
    elif sys.platform.startswith("win"):
        if reveal:
            subprocess.run(["explorer", f"/select,{reveal}"], check=False)
        else:
            os.startfile(str(target))                    # type: ignore[attr-defined]  # noqa
    else:
        subprocess.run(["xdg-open", str(target)], check=False)   # no portable "reveal"
    return {"ok": True, "opened": which, "revealed": reveal.name if reveal else ""}


def _backups_dir(ctx: context.Ctx) -> Path:
    # per-source, sibling of interim/ (a backup snapshots THIS source's interim state)
    return ctx.source_dir / "backups"


def _verify_zip(path: Path, sources: dict[str, Path] | None = None) -> None:
    """Read a backup back and prove it is intact, or raise.

    A zip carries a CRC per member, but only a read checks it — and nothing read a backup
    until the day it was restored, which is exactly when the original is already gone. So
    every archive is read back here, at write time, while the source files are still there
    to compare against. `sources` additionally proves the archive holds the same BYTES as
    the files it claims to snapshot, which a CRC cannot: it only says the member decompresses
    to whatever was compressed."""
    try:
        zf = zipfile.ZipFile(path)
    except Exception as e:
        raise ValueError(f"{path.name}: not a readable archive ({e})") from None
    with zf:
        try:
            bad = zf.testzip()                    # decompresses every member, checks each CRC
        except Exception as e:                    # a damaged deflate stream raises before naming it
            raise ValueError(f"{path.name}: archive is damaged ({e})") from None
        if bad:
            raise ValueError(f"{path.name}: corrupt member {bad}")
        if sources is None:
            return
        names = set(zf.namelist())
        missing = sorted(set(sources) - names)
        if missing:
            raise ValueError(f"{path.name}: {len(missing)} file(s) missing, e.g. {missing[0]}")
        for n, src in sources.items():
            if zf.read(n) != src.read_bytes():
                raise ValueError(f"{path.name}: {n} does not match the file on disk")


def backup_db(ctx: context.Ctx, tag: str = "") -> dict:
    """Snapshot the current source's interim/ jsonl state (the editable source-of-truth
    the bank reads) into a timestamped zip under <source>/backups/. Safe to call before a
    segment re-run so it can be rolled back. `tag` marks why it was taken (e.g. 'pre-clean')."""
    if not ctx.interim_dir.is_dir():
        raise ValueError("no interim dir for this source")
    files = sorted(p for p in ctx.interim_dir.iterdir() if p.is_file())
    if not files:
        raise ValueError("interim empty — nothing to back up")
    n_entries = 0
    for p in files:
        if p.suffix == ".jsonl":
            try:
                n_entries += len(interim_build.read_jsonl(p, required=False))
            except Exception:
                pass
    dest = _backups_dir(ctx)
    dest.mkdir(parents=True, exist_ok=True)
    tag = re.sub(r"[^\w\-]", "", tag)[:20]
    base = f"{ctx.source}_{time.strftime('%Y%m%d-%H%M%S')}" + (f"_{tag}" if tag else "")
    name, k = f"{base}.zip", 1
    while (dest / name).exists():                     # unique even within the same second
        k += 1
        name = f"{base}-{k}.zip"
    tmp = Path(tempfile.mkdtemp(prefix="questgen_bak_"))
    try:
        z = tmp / name
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in files:
                zf.write(p, p.name)               # flat: interim/ has no subdirs
        _verify_zip(z, {p.name: p for p in files})
        shutil.copy2(z, dest / name)
        _verify_zip(dest / name, {p.name: p for p in files})   # and after the copy
    except Exception:
        (dest / name).unlink(missing_ok=True)     # never leave a backup we could not verify
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"ok": True, "file": name, "n": len(files), "entries": n_entries}


def list_backups(ctx: context.Ctx) -> dict:
    """Backups for the CURRENT source, newest first."""
    d = _backups_dir(ctx)
    out = []
    if d.is_dir():
        for p in sorted(d.glob(f"{ctx.source}_*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
            st = p.stat()
            out.append({"file": p.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    return {"backups": out}


def _stage_rank(name: str) -> int:
    return 2 if name.endswith(".tagged.jsonl") else 1 if name.endswith(".clean.jsonl") else 0


def restore_db(ctx: context.Ctx, body: dict) -> dict:
    """Restore a backup zip into the current source's interim/. Auto-snapshots the current
    state first, so a restore is itself reversible.

    Two things the naive "unzip over it" version got wrong, both of which made a restore
    look like it had not happened (the bank kept serving the newer state):
      * extracted files keep the zip's old mtimes -> the freshness rule would still pick a
        NEWER, un-restored later stage. We re-stamp them in stage order instead.
      * a later-stage file the snapshot does not contain is a derivative of the state we
        just rolled back; it is parked as *.superseded (it lives on inside the pre-restore
        backup) rather than left to shadow the restored entries."""
    name = str(body.get("file", ""))
    if "/" in name or "\\" in name or ".." in name or not name.endswith(".zip"):
        raise ValueError("bad backup name")
    if not name.startswith(f"{ctx.source}_"):
        raise ValueError("backup belongs to a different source")
    src_zip = _backups_dir(ctx) / name
    if not src_zip.is_file():
        raise ValueError("no such backup")
    # A restore is only reversible while the pre-restore snapshot exists, so a snapshot that
    # cannot be taken (or cannot be verified) aborts before anything is overwritten. The one
    # tolerated case is having nothing to snapshot: restoring into an empty interim/ has
    # nothing to lose.
    pre = None
    try:
        pre = backup_db(ctx, tag="pre-restore")["file"]
    except Exception as e:
        if "interim" not in str(e):
            raise ValueError(f"restore aborted: could not snapshot the current state first ({e})")
    ctx.interim_dir.mkdir(parents=True, exist_ok=True)
    # Extract and verify EVERYTHING before interim/ is touched. Copying member by member
    # straight out of the archive meant a member that failed to decompress left the earlier
    # ones already written: interim/ became a silent mix of two points in time, and the
    # caller only saw an exception it would read as "nothing happened".
    tmp = Path(tempfile.mkdtemp(prefix="questgen_restore_"))
    try:
        _verify_zip(src_zip)
        with zipfile.ZipFile(src_zip) as zf:
            members = [n for n in zf.namelist()
                       if n and "/" not in n and "\\" not in n and ".." not in n]
            for n in members:
                zf.extract(n, tmp)
        staged = [tmp / n for n in members]
        missing = [n for n, q in zip(members, staged) if not q.is_file()]
        if missing:
            raise ValueError(f"restore aborted: {len(missing)} member(s) did not extract")
        for n, q in zip(members, staged):         # only now is interim/ modified
            shutil.copy2(q, ctx.interim_dir / n)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # Re-stamp mtimes so the snapshot's OWN stage ranking comes back: order by the
    # timestamps recorded in the zip, ties broken by stage (the freshness rule prefers
    # the later stage on a tie, and zip clocks only have 2s resolution).
    with zipfile.ZipFile(src_zip) as zf:
        stamps = {n: zf.getinfo(n).date_time for n in members}
    jl = sorted((m for m in members if m.endswith(".jsonl")),
                key=lambda n: (stamps.get(n, (0,) * 6), _stage_rank(n)))
    base = time.time() - len(jl) - 1
    for k, n in enumerate(jl):
        ts = base + k
        try:
            os.utime(ctx.interim_dir / n, (ts, ts))
        except OSError:
            pass
    parked = []
    snap = set(members)
    for stem in {n.split(".")[0] for n in members if n.endswith(".jsonl")}:
        for stage in ("clean", "tagged"):
            p = interim_build.stage_path(ctx, stem, stage)
            if p.is_file() and p.name not in snap:
                os.replace(p, p.with_name(p.name + ".superseded"))
                parked.append(p.name)
    return {"ok": True, "restored": len(members), "parked": parked, "pre_backup": pre}


# ---------------------------------------------------------------- bank reads
# The bank is re-read on every tab switch / edit / verify, and it is the biggest payload
# the dashboard moves. Parsing ~1k entries per request was a visible stall, so entries are
# parsed once per interim state and cached under a (name, mtime, size) signature — any
# write, restore or pipeline run changes the signature and invalidates it.

_BANK_CACHE: dict[str, tuple] = {}
_BANK_CACHE_MAX = 4
BANK_WARNINGS: dict[str, list] = {}      # source -> files that could not be read in full


def _interim_sig(ctx: context.Ctx) -> tuple:
    if not ctx.interim_dir.is_dir():
        return ()
    out = []
    for p in sorted(ctx.interim_dir.glob("*.jsonl")):
        st = p.stat()
        out.append((p.name, st.st_mtime_ns, st.st_size))
    return tuple(out)


def bank_entries(ctx: context.Ctx) -> list[dict]:
    """Live entries for this source: each stem's newest stage (interim_build.newest_stage),
    label-canonicalised, each carrying `_rev` (the editor's concurrency token).
    Files are prefetched in parallel first — on an iCloud-synced tree each cold file costs
    ~1-2 s to materialise, and serially that is a minute of "loading" per source. A stem
    whose file cannot be read completely is reported in `warnings` and NOT cached, so a
    reload can recover it instead of the truncated read sticking."""
    if not ctx.interim_dir.is_dir():
        return []
    key = str(ctx.source_dir)
    sig = _interim_sig(ctx)
    hit = _BANK_CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    stems = sorted({p.name.split(".")[0] for p in ctx.interim_dir.glob("*.jsonl")})
    live = [(stem, *interim_build.newest_stage(ctx, stem)) for stem in stems]
    interim_build.prefetch([p for _s, p, _st in live if p is not None])
    rows, warnings = [], []
    for stem, use, stage in live:
        if use is None:
            continue
        try:
            entries = interim_build.read_jsonl(use)
        except Exception as e:
            warnings.append(f"{use.name}: {e}")
            continue
        for r in entries:
            interim_build.canon_entry(r)      # legacy files: A/B/C/D options, bare '(a)'
            r["_rev"] = entry_rev(r)
            r["file_stem"], r["stage"], r["cleaned"] = stem, stage, stage in ("clean", "tagged")
            rows.append(r)
    BANK_WARNINGS[key] = warnings
    if warnings:
        return rows                            # incomplete read: never cache it
    _BANK_CACHE[key] = (sig, rows)
    while len(_BANK_CACHE) > _BANK_CACHE_MAX:
        _BANK_CACHE.pop(next(iter(_BANK_CACHE)))
    return rows


def _usage_path(ctx: context.Ctx) -> Path:
    return ctx.stage_dir / "db" / "usage.json"


def read_usage(ctx: context.Ctx) -> dict:
    try:
        d = json.loads(_usage_path(ctx).read_text(encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("version", 1)
    d.setdefault("questions", {})
    d.setdefault("exports", [])
    return d


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="questgen_json_"))
    try:
        tf = tmp / path.name
        tf.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        shutil.copy2(tf, path)                       # fuse mount: write local, then copy
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def usage_map(ctx: context.Ctx) -> dict:
    """{qid: {count, first, last, titles}} for the CURRENT level/source."""
    pre = f"{ctx.level}/{ctx.source}/"
    return {k[len(pre):]: v for k, v in read_usage(ctx)["questions"].items() if k.startswith(pre)}


USAGE_EXPORTS_KEEP = 300


def log_usage(ctx: context.Ctx, qids: list, title: str, note: str = "", kind: str = "export") -> dict:
    """Record that these questions were actually used: bump each question's count and
    append one usage record (also appended to db/usage_log.jsonl as an audit trail)."""
    qids = [str(q) for q in qids if str(q).strip()]
    if not qids:
        raise ValueError("没有题目可记录")
    d = read_usage(ctx)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    for q in qids:
        k = f"{ctx.level}/{ctx.source}/{q}"
        rec = d["questions"].setdefault(k, {"count": 0, "first": ts, "last": ts, "titles": []})
        rec["count"] = int(rec.get("count", 0)) + 1
        rec["last"] = ts
        rec.setdefault("first", ts)
        if title and title not in rec["titles"]:
            rec["titles"] = (rec["titles"] + [title])[-10:]
    entry = {"ts": ts, "kind": kind, "title": title, "note": note, "level": ctx.level,
             "source": ctx.source, "n": len(qids), "qids": qids}
    d["exports"] = (d["exports"] + [entry])[-USAGE_EXPORTS_KEEP:]
    _write_json(_usage_path(ctx), d)
    logp = _usage_path(ctx).with_name("usage_log.jsonl")
    try:
        with open(logp, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return {"ok": True, "n": len(qids), "ts": ts}


def log_usage_entries(entries: list, title: str, note: str = "", kind: str = "export") -> dict:
    """Log usage for resolved entries, grouping by their own source (from resolve_selection's
    _src) so each source's usage.json is updated — a cross-source worksheet counts in each."""
    if not entries:
        raise ValueError("没有题目可记录")
    groups: dict = {}
    for e in entries:
        sc = e.get("_src") or {}
        key = (sc.get("s"), sc.get("t"), sc.get("l"), sc.get("src"))
        groups.setdefault(key, []).append(e["qid"])
    n = 0
    for (s, t, l, src), qids in groups.items():
        if None in (s, t, l, src):
            continue
        n += log_usage(context.Ctx(s, t, l, src), qids, title, note, kind)["n"]
    return {"ok": True, "n": n, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


def clear_usage(ctx: context.Ctx, body: dict) -> dict:
    """Undo usage records — for a list of qids, or the whole current source (a mis-click
    on 'record as used' should not permanently distort the history)."""
    d = read_usage(ctx)
    pre = f"{ctx.level}/{ctx.source}/"
    qids = [str(q) for q in (body.get("qids") or [])]
    if qids:
        gone = [k for k in list(d["questions"]) if k.startswith(pre) and k[len(pre):] in qids]
    else:
        gone = [k for k in list(d["questions"]) if k.startswith(pre)]
    for k in gone:
        d["questions"].pop(k, None)
    _write_json(_usage_path(ctx), d)
    return {"ok": True, "cleared": len(gone)}


def usage_state(ctx: context.Ctx) -> dict:
    d = read_usage(ctx)
    recent = [e for e in d["exports"] if e.get("source") == ctx.source][-20:]
    return {"usage": usage_map(ctx), "exports": list(reversed(recent))}


# ------------------------------------------------------- pipeline overwrite guard
# interim/clean/tag all REWRITE bank entries for the files they run on, and an interim
# re-run additionally demotes clean/tagged (they become stale derivatives). Human edits,
# verifications and hand-added figures cannot be regenerated, so a run over a non-empty
# bank must be confirmed, with the backup offered right there.

BANK_STEPS = {"interim", "clean", "tag", "all"}
# language-neutral stage labels (shown verbatim in the overwrite-warning dialog)
STEP_TARGET = {"interim": "raw → clean/tagged", "clean": "clean", "tag": "tagged",
               "all": "raw → clean → tagged"}


def bank_risk(ctx: context.Ctx, names: list, step: str) -> dict:
    """What a pipeline run would overwrite: per file the live entry count and how much
    human work it carries (edits / verifications)."""
    out = {"step": step, "target": STEP_TARGET.get(step, step), "files": [],
           "entries": 0, "edited": 0, "verified": 0}
    if step not in BANK_STEPS:
        return out
    for n in names:
        stem = Path(str(n)).stem
        if stem.endswith("_ans"):
            continue
        p, stage = interim_build.newest_stage(ctx, stem)
        if p is None:
            continue
        try:
            rows = _read_jsonl(p)
        except Exception:
            continue
        if not rows:
            continue
        ed = sum(1 for r in rows if "human_edited" in (r.get("flags") or []))
        vf = sum(1 for r in rows if "verified" in (r.get("flags") or []))
        out["files"].append({"file": stem, "stage": stage, "n": len(rows),
                             "edited": ed, "verified": vf})
        out["entries"] += len(rows)
        out["edited"] += ed
        out["verified"] += vf
    return out


def resolve_selection(ctx: context.Ctx, body: dict) -> list[dict]:
    """Resolve a worksheet selection into entries, IN ORDER, across sources.

    The cart can hold questions from several sources (it persists as you switch source),
    and qids are NOT globally unique (212 collide across sources here), so each item must
    carry its own source. Accepts either:
      body["items"] = [{s,t,l,src,qid}, ...]   (source-qualified — the correct form), or
      body["qids"]  = [qid, ...]               (legacy: all resolved against the current ctx).
    Each returned entry gets `_img_dirs` (its own source's extraction dirs) and `_src`
    (its source coords, for per-source usage logging)."""
    items = body.get("items")
    if not isinstance(items, list) or not items:
        items = [{"s": ctx.subject, "t": ctx.stage, "l": ctx.level, "src": ctx.source, "qid": q}
                 for q in (body.get("qids") or [])]
    pools: dict = {}                                  # (s,t,l,src) -> {qid: entry}
    out = []
    for it in items:
        key = (str(it.get("s") or ctx.subject), str(it.get("t") or ctx.stage),
               str(it.get("l") or ctx.level), str(it.get("src") or ctx.source))
        qid = str(it.get("qid") or "")
        if key not in pools:
            sctx = context.Ctx(*key)
            pools[key] = ({r["qid"]: r for r in bank_entries(sctx)}, sctx)
        pool, sctx = pools[key]
        e = pool.get(qid)
        if e is None:
            continue
        e = dict(e, _img_dirs=[str(d) for d in export_docx.entry_img_dirs(sctx, e)],
                 _src={"s": key[0], "t": key[1], "l": key[2], "src": key[3]})
        out.append(e)
    return out


def start_job(name: str, fn) -> None:
    """fn(log, cancel_event). One job at a time."""
    global JOB
    with JOB_LOCK:
        if JOB.get("state") == "running":
            raise RuntimeError(f"job already running: {JOB.get('name')}")
        CANCEL.clear()
        JOB = {"state": "running", "name": name, "log": [], "t0": time.time()}

    def log(msg):
        JOB["log"].append(str(msg))

    def target():
        try:
            fn(log, CANCEL)
            JOB["state"] = "cancelled" if CANCEL.is_set() else "done"
        except mineru_extract.Cancelled:
            JOB["state"] = "cancelled"
            log("-- cancelled --")
        except Exception as e:
            log(f"ERROR: {e}")
            JOB["state"] = "error"
        JOB["dt"] = round(time.time() - JOB["t0"], 1)

    threading.Thread(target=target, daemon=True).start()


# ---------------------------------------------------------------- handler

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")   # bank/job state is never stale-servable
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, code=400):
        self._json({"error": str(msg)}, code)

    # ---- GET
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/":
                body = (STATIC_DIR / "index.html").read_text(encoding="utf-8").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path.startswith("/static/"):
                rel = u.path[len("/static/"):]
                fp = (STATIC_DIR / rel).resolve()
                if ".." in rel or not str(fp).startswith(str(STATIC_DIR) + os.sep) or not fp.is_file():
                    self.send_response(404); self.end_headers(); self.wfile.write(b"not found"); return
                data = fp.read_bytes()
                ctype = STATIC_TYPES.get(fp.suffix, "application/octet-stream")
                if ctype.startswith("text/") or "javascript" in ctype or "json" in ctype:
                    ctype += "; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-cache, must-revalidate")   # never serve stale JS/CSS
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif u.path == "/api/sources":
                self._json(context.discover(context.content_root()))
            elif u.path == "/api/files":
                self._json(list_files(ctx_from_query(q)))
            elif u.path == "/api/open_folder":
                self._json(open_in_file_manager(ctx_from_query(q), q.get("which", ["original"])[0],
                                                q.get("f", [""])[0]))
            elif u.path == "/api/ops":
                self._json(source_ops.load_ops(ctx_from_query(q).source_dir))
            elif u.path == "/api/map":
                ctx = ctx_from_query(q)
                self._json(source_ops.page_map(ctx.source_dir, q["f"][0]))
            elif u.path == "/api/pdf":
                self.serve_pdf(ctx_from_query(q), safe_rel(q["f"][0]))
            elif u.path == "/api/pipeline":
                self._json(pipeline_status(ctx_from_query(q)))
            elif u.path == "/api/job":
                frm = int(q.get("from", ["0"])[0])
                self._json({"state": JOB["state"], "name": JOB.get("name"),
                            "dt": JOB.get("dt"), "log": JOB["log"][frm:], "n": len(JOB["log"])})
            elif u.path == "/api/bank":
                ctx = ctx_from_query(q)
                ents = bank_entries(ctx)
                self._json({"entries": ents,
                            "warnings": BANK_WARNINGS.get(str(ctx.source_dir)) or []})
            elif u.path == "/api/backups":
                self._json(list_backups(ctx_from_query(q)))
            elif u.path == "/api/usage":
                self._json(usage_state(ctx_from_query(q)))
            elif u.path == "/api/edit_journal":
                self._json(edit_journal(ctx_from_query(q), str(q.get("qid", [""])[0]),
                                        int(q.get("limit", ["40"])[0])))
            elif u.path == "/api/config":
                self._json(read_config())
            elif u.path == "/api/problem_types":
                self._json({"types": list(llm_tag.problem_types())})
            elif u.path == "/api/gen_pending":
                self._json(gen_pending(ctx_from_query(q)))
            elif u.path == "/api/gen_refs":
                self._json(gen_refs(ctx_from_query(q), str(q.get("ts", [""])[0])))
            elif u.path == "/api/answer_ref":
                # per-qno answer/solution the LLM parsed from the _ans file (segment
                # sidecar) — read-only reference for manual editing.
                ctx = ctx_from_query(q)
                stem = str(q.get("stem", [""])[0])
                p = ctx.interim_dir / f"{stem}.answers.json"
                self._json(json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {})
            elif u.path == "/api/taxonomy":
                ctx = ctx_from_query(q)
                try:
                    tax = llm_tag.load_taxonomy(ctx)
                    self._json({kp["id"]: {"name": kp.get("name_en", ""),
                                           "los": kp.get("learning_outcomes") or []}
                                for kp in tax.values()})
                except Exception:
                    self._json({})
            elif u.path == "/api/img":
                ctx = ctx_from_query(q)
                rel = q["f"][0].replace("\\", "/")
                p = (ctx.extracted_dir / rel).resolve()
                if not p.is_file() and "/" in rel:
                    # solution images live under <stem>_ans/
                    head, tail = rel.split("/", 1)
                    p = (ctx.extracted_dir / f"{head}_ans" / tail).resolve()
                if ".." in rel or not str(p).startswith(str(ctx.extracted_dir.resolve())) \
                        or not p.is_file() or p.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                    return self._err("no such image", 404)
                data = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg" if p.suffix != ".png" else "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            else:
                self._err("not found", 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._err(e, 500)
            except Exception:
                pass

    def serve_pdf(self, ctx: context.Ctx, rel: str):
        p = (ctx.source_dir / rel).resolve()
        if not str(p).startswith(str(ctx.source_dir.resolve())) or not p.is_file():
            return self._err("no such file", 404)
        interim_build.materialize(p)   # iCloud-evicted PDF: make it local before ranging
        size = p.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
        partial = bool(rng and m)
        if partial:
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:  # suffix range: last N bytes
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(p, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    # ---- POST
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            ctx = ctx_from_query(q)
            if u.path == "/api/save":
                plan = body["plan"]
                for step in plan["steps"]:
                    step["source"] = safe_rel(step["source"])
                with LOCK:
                    out = source_ops.apply_plan(ctx.source_dir, plan)
                with fitz.open(out) as d:
                    np = d.page_count
                self._json({"ok": True, "output": out.name, "pages": np})
            elif u.path == "/api/delete":
                rel = safe_rel(body["f"])
                if not rel.startswith("raw/"):
                    raise ValueError("only raw/ files can be deleted")
                p = ctx.source_dir / rel
                if p.is_file():
                    p.unlink()
                ops = source_ops.load_ops(ctx.source_dir)
                if ops["outputs"].pop(Path(rel).name, None) is not None:
                    (ctx.raw_dir / "ops.json").write_text(
                        json.dumps(ops, ensure_ascii=False, indent=1), encoding="utf-8")
                self._json({"ok": True})
            elif u.path == "/api/run":
                step = body.get("step")
                names = body.get("files") or []
                for n in names:
                    if "/" in n or ".." in n:
                        raise ValueError(f"bad name: {n}")
                if step in ("extract", "interim", "clean", "tag", "validate", "all"):
                    # Guard: interim/clean/tag REWRITE the bank for these files. Ask before
                    # overwriting an existing bank, and offer the snapshot in the same breath
                    # (the client re-posts with confirm/backup_first).
                    risk = bank_risk(ctx, names, step)
                    if risk["entries"] and not body.get("confirm"):
                        return self._json({"ok": False, "needs_confirm": True, "risk": risk,
                                           "backups": len(list_backups(ctx)["backups"])})
                    backed_up = None
                    if risk["entries"] and body.get("backup_first"):
                        with LOCK:
                            backed_up = backup_db(ctx, tag=f"pre-{step}")
                    # hot-reload pipeline modules so code edits apply without
                    # restarting the long-running dashboard process
                    import importlib
                    for m in (context, source_ops, mineru_extract, interim_build, llm_clean,
                              llm_segment, llm_tag, bank):
                        importlib.reload(m)
                    use_vlm = bool(body.get("use_vlm"))
                    thinking = True if body.get("thinking") else False
                    force = bool(body.get("force"))
                    chem = bool(body.get("chem"))

                    def fn(log, cancel, step=step, ctx=ctx, names=names,
                           use_vlm=use_vlm, thinking=thinking, force=force, chem=chem):
                        if step in ("extract", "all"):
                            mineru_extract.extract_files(ctx, names, log=log, cancel=cancel, force=force)
                        if step in ("interim", "all") and not cancel.is_set():
                            llm_segment.build_files(ctx, names, log=log, cancel=cancel, thinking=thinking)
                        if step in ("clean", "all") and not cancel.is_set():
                            llm_clean.clean_files(ctx, names, log=log, cancel=cancel,
                                                  use_vlm=use_vlm, thinking=thinking, chem=chem)
                        if step in ("tag", "all") and not cancel.is_set():
                            llm_tag.tag_files(ctx, names, log=log, cancel=cancel, thinking=thinking)
                        if step in ("validate", "all") and not cancel.is_set():
                            bank.check(ctx, log=log)
                    start_job(step, fn)
                else:
                    raise ValueError(f"unknown step: {step}")
                self._json({"ok": True, "backup": (backed_up or {}).get("file")})
            elif u.path == "/api/cancel":
                CANCEL.set()
                self._json({"ok": True})
            elif u.path == "/api/export":
                if export_docx is None:
                    raise RuntimeError(f"docx export unavailable: {EXPORT_ERR}")
                import importlib
                importlib.reload(export_docx)
                title = re.sub(r"[^\w\- ]", "", body.get("title") or "Worksheet").strip() or "Worksheet"
                entries = resolve_selection(ctx, body)     # across sources, in order
                if not entries:
                    raise ValueError("no valid qids")
                out = ctx.outputs_dir / f"{title}.docx"
                mcq = str(body.get("mcq_label") or "letter_bare")
                blank = str(body.get("blank") or "dots")
                cap = body.get("caption") if isinstance(body.get("caption"), dict) else {}
                # answer_format: none | end (answers table appended) | teacher (separate red
                # inline copy) | both. `with_solutions` kept for back-compat (= "end").
                afmt = str(body.get("answer_format")
                           or ("end" if body.get("with_solutions") else "none"))
                common = dict(mcq_label=mcq, blank=blank, marks_col=bool(body.get("marks_col")),
                              caption=cap, sections=bool(body.get("sections")),
                              show_total=bool(body.get("show_total")))
                files = []
                with LOCK:
                    export_docx.build_docx(ctx, entries, title, afmt in ("end", "both"), out, **common)
                    files.append(out.name)
                    if afmt in ("teacher", "both"):
                        tout = ctx.outputs_dir / f"{title} (answers).docx"
                        export_docx.build_docx(ctx, entries, title, False, tout, teacher=True, **common)
                        files.append(tout.name)
                used = None
                if body.get("log_usage"):          # user confirmed: this goes to real teaching
                    with LOCK:
                        used = log_usage_entries(entries, title, str(body.get("note") or ""), "export")
                self._json({"ok": True, "file": files[0], "files": files, "n": len(entries),
                            "logged": bool(used)})
            elif u.path == "/api/log_usage":
                with LOCK:
                    ents = resolve_selection(ctx, body)
                    self._json(log_usage_entries(ents, str(body.get("title") or "").strip(),
                                                 str(body.get("note") or ""),
                                                 str(body.get("kind") or "manual")))
            elif u.path == "/api/clear_usage":
                with LOCK:
                    self._json(clear_usage(ctx, body))
            elif u.path == "/api/restore_entry":
                with LOCK:
                    self._json(restore_entry(ctx, body))
            elif u.path == "/api/edit_entry":
                with LOCK:                        # one entry write at a time (see entry writes)
                    self._json(save_edit(ctx, body))
            elif u.path == "/api/add_entry":
                with LOCK:
                    self._json(add_entry(ctx, body))
            elif u.path == "/api/delete_entry":
                with LOCK:
                    self._json(delete_entry(ctx, body))
            elif u.path == "/api/toggle_verified":
                with LOCK:
                    self._json(toggle_verified(ctx, body))
            elif u.path == "/api/config":
                with LOCK:
                    self._json(write_config(body))
            elif u.path == "/api/generate":
                with LOCK:
                    self._json(generate_questions(ctx, body))
            elif u.path == "/api/gen_accept":
                with LOCK:
                    self._json(gen_accept(ctx, body))
            elif u.path == "/api/gen_reject":
                with LOCK:
                    self._json(gen_reject(ctx, body))
            elif u.path == "/api/backup_db":
                with LOCK:
                    self._json(backup_db(ctx))
            elif u.path == "/api/restore_db":
                with LOCK:
                    self._json(restore_db(ctx, body))
            elif u.path == "/api/upload_img":
                self._json(upload_img(ctx, body))
            elif u.path == "/api/upload_source":
                with LOCK:
                    self._json(upload_source_file(body))
            elif u.path == "/api/ai_assist":
                self._json(ai_assist(body))
            else:
                self._err("not found", 404)
        except Exception as e:
            self._err(e, 500)


# ---------------------------------------------------------------- frontend

# UI is a small static frontend under static/ (index.html + dashboard.css + dashboard.js),
# served by do_GET; index.html is the shell, /static/<file> serves the css/js. All three are
# read per request, so an edit shows up on refresh — the shell used to be read at import and
# needed a restart, which silently served a stale UI while css/js hot-reloaded around it.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"   # questgen/static (sibling of scripts/)
STATIC_TYPES = {".css": "text/css", ".js": "application/javascript", ".html": "text/html",
                ".svg": "image/svg+xml", ".map": "application/json", ".woff2": "font/woff2"}


if __name__ == "__main__":
    print(f"questgen dashboard → http://{HOST}:{PORT}")
    srv = ThreadingHTTPServer((HOST, PORT), H)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        srv.shutdown()
        srv.server_close()          # release the port immediately (no restart bind error)
