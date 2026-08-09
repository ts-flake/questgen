"""M4 build DB: tagged entries → db/questgen.sqlite (queryable by topic/type/difficulty)

Loads every interim/*.tagged.jsonl (falls back to *.clean.jsonl / *.jsonl if a file
wasn't tagged), validates each entry against the final schema (required fields + topic
membership in the stage taxonomy), and writes a SQLite index. JSONL stays the source
of truth; the DB is a rebuildable derivation. Invalid rows are reported, never silently
loaded. Built in a local temp dir then copied (fuse locking on the mount).

Tables:
  problems(qid PK, source_id, file, qno, kind, type, difficulty, stem, parts_json,
           options_json, answer_json, solution, imgs_json, flags_json, marks)
  problem_topics(qid, topic)          -- one row per topic (many-to-many)

CLI: python3 scripts/build_db.py [--source ...]   (or --all-sources to sweep the tree)
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

import context
import interim_build as ib
import llm_tag

REQUIRED = ("qid", "kind", "stem", "meta")


def taggable_source(ctx: context.Ctx, stem: str) -> Path | None:
    """The live stage file for a stem — same resolution the dashboard bank uses
    (interim_build.newest_stage), so DB and bank can never disagree."""
    return ib.newest_stage(ctx, stem)[0]


def collect_source(ctx: context.Ctx) -> list[dict]:
    """All entries for one source, from each stem's live stage file."""
    if not ctx.interim_dir.is_dir():
        return []
    stems = sorted({p.name.split(".")[0] for p in ctx.interim_dir.glob("*.jsonl")})
    live = [taggable_source(ctx, stem) for stem in stems]
    ib.prefetch([p for p in live if p is not None])   # parallel materialise, then read
    rows = []
    for src in live:
        if not src:
            continue
        for r in ib.read_jsonl(src):        # whole-file verified read (iCloud dataless)
            ib.canon_entry(r)          # legacy files may still hold A/B/C/D or bare '(a)'
            rows.append(r)
    return rows


def stage_sources(ctx: context.Ctx) -> list[context.Ctx]:
    """Every source under this subject/stage (all levels) — the DB aggregates them,
    since they share one taxonomy (_taxonomy is per subject/stage)."""
    out = []
    for rec in context.discover(ctx.root):
        if rec["subject"] == ctx.subject and rec["stage"] == ctx.stage:
            out.append(context.Ctx(rec["subject"], rec["stage"], rec["level"], rec["source"], ctx.root))
    return out


def collect(ctx: context.Ctx) -> list[dict]:
    """All entries across the subject/stage (every level & source)."""
    rows = []
    for sctx in stage_sources(ctx):
        for r in collect_source(sctx):
            r.setdefault("meta", {})["level"] = sctx.level
            rows.append(r)
    return rows


def validate(entry: dict, tax: set) -> list[str]:
    errs = []
    for k in REQUIRED:
        if not entry.get(k):
            errs.append(f"missing:{k}")
    for t in (entry.get("tags", {}).get("topic") or []):
        if t not in tax:
            errs.append(f"bad_topic:{t}")
    return errs


DDL = """
CREATE TABLE problems(
  gid TEXT PRIMARY KEY, qid TEXT, source_id TEXT, level TEXT, file TEXT, qno TEXT, kind TEXT,
  type TEXT, difficulty TEXT, stem TEXT, parts_json TEXT, options_json TEXT,
  answer_json TEXT, solution TEXT, imgs_json TEXT, flags_json TEXT, marks INTEGER);
CREATE TABLE problem_topics(gid TEXT, topic TEXT);
CREATE INDEX ix_topic ON problem_topics(topic);
CREATE INDEX ix_src ON problems(source_id);
CREATE INDEX ix_diff ON problems(difficulty);
CREATE INDEX ix_type ON problems(type);
"""


def build(ctx: context.Ctx, log=print) -> dict:
    try:
        tax = set(llm_tag.load_taxonomy(ctx))
    except FileNotFoundError:
        tax = set()
    rows = collect(ctx)
    all_topics = set(t for x in rows for t in (x.get("tags", {}).get("topic") or []))
    good, invalid, seen = [], [], set()
    for r in rows:
        gid = f"{r.get('meta', {}).get('source_id', '?')}/{r['qid']}"   # globally unique
        if gid in seen:
            invalid.append((gid, ["duplicate"]))
            continue
        seen.add(gid)
        r["_gid"] = gid
        errs = validate(r, tax or all_topics)
        if errs and any(e.startswith("missing") for e in errs):
            invalid.append((gid, errs))
            continue
        good.append(r)

    tmp = Path(tempfile.mkdtemp(prefix="questgen_db_"))
    dbfile = tmp / "questgen.sqlite"
    con = sqlite3.connect(dbfile)
    con.executescript(DDL)
    for r in good:
        tg = r.get("tags", {})
        m = r["meta"]
        con.execute(
            "INSERT INTO problems VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["_gid"], r["qid"], m.get("source_id"), m.get("level"), m.get("file"),
             m.get("qno"), r["kind"], tg.get("type"), tg.get("difficulty"), r["stem"],
             json.dumps(r.get("parts"), ensure_ascii=False),
             json.dumps(r.get("options"), ensure_ascii=False),
             json.dumps(r.get("answer"), ensure_ascii=False),
             r.get("solution"),
             json.dumps(r.get("imgs"), ensure_ascii=False),
             json.dumps(r.get("flags"), ensure_ascii=False),
             m.get("marks")))
        for t in (tg.get("topic") or []):
            con.execute("INSERT INTO problem_topics VALUES (?,?)", (r["_gid"], t))
    con.commit()
    con.close()

    ctx.stage_dir.joinpath("db").mkdir(parents=True, exist_ok=True)
    out = ctx.stage_dir / "db" / "questgen.sqlite"
    shutil.copy2(dbfile, out)
    shutil.rmtree(tmp, ignore_errors=True)
    log(f"  {ctx.subject}/{ctx.stage}: {len(good)} loaded, {len(invalid)} invalid -> {out}")
    if invalid:
        for qid, errs in invalid[:10]:
            log(f"    invalid {qid}: {errs}")
    return {"source": ctx.source, "loaded": len(good), "invalid": len(invalid),
            "invalid_ids": [q for q, _ in invalid]}


def build_files(ctx: context.Ctx, names=None, log=print, cancel=None) -> dict:
    """names ignored (DB is per-source); kept for dashboard step-runner signature."""
    return build(ctx, log)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--all-sources", action="store_true", help="every source in the tree")
    context.add_ctx_args(ap)
    a = ap.parse_args()
    if a.all_sources:
        root = context.content_root(a.content_root)
        for s in context.discover(root):
            build(context.Ctx(s["subject"], s["stage"], s["level"], s["source"], root))
    else:
        build(context.ctx_from_args(a))
