"""M4 tagging: read a clean entry's stem, add pedagogy tags → interim/*.tagged.jsonl

Mirrors v0 role-A: the LLM ONLY classifies — it never edits stem/answer/options.
Per entry it assigns, from the stage's closed taxonomy:
  - topic:      1-2 knowledge-point ids (validated against the taxonomy; no invented ids)
  - type:       problem_type (closed vocab from config.yaml `problem_types:`, editable in dashboard)
  - difficulty: basic | medium | advance

Tags are merged into a `tags` block; verbatim fields are untouched. Topic membership
is validated on write; invalid ids are dropped and flagged. Output *.tagged.jsonl
(supersedes *.clean.jsonl as the taggable source) + *.tag.log.json audit.

Taxonomy: content/<subject>/<stage>/_taxonomy/knowledge_tree.yaml (closed vocab).
Endpoint: config.yaml `llm:`. QUESTGEN_LLM_MOCK=1 = offline plumbing test.

CLI: python3 scripts/llm_tag.py --all [--source ...]
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import context
import llm_clean

DIFFICULTY = ("basic", "medium", "advance")
DEFAULT_PROBLEM_TYPES = ("mcq", "short_answer", "structured", "word_problem")


def problem_types() -> tuple:
    """Closed problem-type vocabulary, read live from config.yaml (`problem_types:`), so a
    dashboard edit takes effect without restart. Falls back to the built-in default."""
    v = context.CONFIG.get("problem_types")
    return tuple(v) if v else DEFAULT_PROBLEM_TYPES


SYS_TMPL = """You classify pre-extracted exam questions. You NEVER change wording, never split/merge, never
touch answers — you ONLY assign tags, output strict JSON.

You are given TOPICS (the ONLY allowed topic ids) and ENTRIES. For EACH entry emit one object:
{{"qid":"<copy exactly>",
 "topic":["<1-2 ids, ONLY from TOPICS, the finest applicable>"],
 "type":"{types}",
 "difficulty":"basic|medium|advance"}}

Rules:
1. topic ids MUST be exact ids from TOPICS. Never invent. If unsure, still pick the single closest.
2. type: MUST be one of: {types}. Use the entry's structure — mcq if it has options; structured if it
   has labelled parts (a),(b); word_problem for a real-world scenario needing setup; otherwise the most
   specific remaining type. Prefer the most specific.
3. difficulty: basic = single-step recall/definition; medium = routine multi-step; advance = multi-concept,
   non-routine, or heavy reasoning.
4. Output ONLY a JSON array, same count/order/ids as ENTRIES. No prose, no code fences."""


def _sys() -> str:
    return SYS_TMPL.format(types="|".join(problem_types()))


def load_taxonomy(ctx: context.Ctx) -> dict:
    import yaml
    p = ctx.root / ctx.subject / ctx.stage / "_taxonomy" / "knowledge_tree.yaml"
    if not p.is_file():
        raise FileNotFoundError(f"no taxonomy: {p}")
    tree = yaml.safe_load(p.read_text(encoding="utf-8"))
    return {kp["id"]: kp for kp in tree.get("knowledge_points", []) if kp.get("status", "active") == "active"}


def topic_menu(tax: dict) -> str:
    return "\n".join(f"{kp['id']} | {kp['name_en']} | kw: {', '.join(kp.get('keywords', [])[:8])}"
                     for kp in tax.values())


def entry_view(r: dict) -> dict:
    v = {"qid": r["qid"], "kind": r["kind"], "stem": r["stem"][:800]}
    if r.get("parts"):
        v["parts"] = [p.get("no", "") for p in r["parts"]]
    if r.get("options"):
        v["options"] = list(r["options"].keys())
    return v


def source_jsonl(ctx: context.Ctx, stem: str) -> Path | None:
    """Prefer clean over raw interim as the taggable source."""
    for name in (f"{stem}.clean.jsonl", f"{stem}.jsonl"):
        p = ctx.interim_dir / name
        if p.is_file():
            return p
    return None


def tag_one(ctx: context.Ctx, stem: str, tax: dict, ep, log=print, cancel=None,
            batch=15) -> dict:
    src = source_jsonl(ctx, stem)
    import interim_build as ib
    rows = ib.read_jsonl(src)                  # whole-file verified read: a streaming read
    #                                            of an iCloud-evicted file yields nothing,
    #                                            and these rows are written straight back
    menu = topic_menu(tax)
    audit = []
    stats = {"tagged": 0, "bad_topic": 0, "llm_errors": 0}

    for i in range(0, len(rows), batch):
        if cancel is not None and cancel.is_set():
            break
        chunk = rows[i:i + batch]
        if os.environ.get("QUESTGEN_LLM_MOCK"):        # heuristic tags for plumbing
            first = next(iter(tax))
            ann = {r["qid"]: {"qid": r["qid"], "topic": [first],
                              "type": "mcq" if r["kind"] == "mcq" else "structured" if r.get("parts") else "short_answer",
                              "difficulty": "medium"} for r in chunk}
        else:
            user = (f"TOPICS:\n{menu}\n\nENTRIES:\n"
                    + json.dumps([entry_view(r) for r in chunk], ensure_ascii=False))
            v = llm_clean.chat(ep, _sys(), user)
            ann = {a.get("qid"): a for a in v if isinstance(a, dict)} if isinstance(v, list) else {}
        for r in chunk:
            a = ann.get(r["qid"])
            if not a:
                stats["llm_errors"] += 1
                r.setdefault("flags", []).append("untagged")
                continue
            topics = [t for t in (a.get("topic") or []) if isinstance(t, str)]
            valid = [t for t in topics if t in tax]
            bad = [t for t in topics if t not in tax]
            if bad:
                stats["bad_topic"] += 1
                r.setdefault("flags", []).append("topic_invalid")
            typ = a.get("type") if a.get("type") in problem_types() else (
                "mcq" if r["kind"] == "mcq" else "structured" if r.get("parts") else "short_answer")
            diff = a.get("difficulty") if a.get("difficulty") in DIFFICULTY else "medium"
            r["tags"] = {"topic": valid, "type": typ, "difficulty": diff}
            if not valid:
                r.setdefault("flags", []).append("no_topic")
            stats["tagged"] += 1
            audit.append({"qid": r["qid"], "topic": valid, "type": typ, "difficulty": diff,
                          **({"dropped": bad} if bad else {})})
        log(f"  {stem}: {min(i+batch, len(rows))}/{len(rows)} tagged")

    with open(ctx.interim_dir / f"{stem}.tagged.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (ctx.interim_dir / f"{stem}.tag.log.json").write_text(
        json.dumps({"summary": {"file": stem, **stats}, "audit": audit},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  {stem}: tagged {stats['tagged']}, bad_topic {stats['bad_topic']}, errors {stats['llm_errors']}")
    return {"file": stem, **stats}


def tag_files(ctx: context.Ctx, names: list[str], log=print, cancel=None, thinking=None) -> dict:
    ep = llm_clean.endpoint(thinking)
    if ep is None and not os.environ.get("QUESTGEN_LLM_MOCK"):
        log("llm_tag: no endpoint (config.yaml llm:) — skipped")
        return {"ok": [], "failed": {}, "skipped": True}
    try:
        tax = load_taxonomy(ctx)
    except FileNotFoundError as e:
        log(f"llm_tag: {e}")
        return {"ok": [], "failed": {n: "no taxonomy" for n in names}}
    log(f"llm_tag: {ep['model'] if ep else 'MOCK'} | taxonomy {len(tax)} topics")
    ok, failed = [], {}
    for n in names:
        if cancel is not None and cancel.is_set():
            break
        stem = Path(n).stem
        if stem.endswith("_ans"):
            continue
        if source_jsonl(ctx, stem) is None:
            log(f"skip (no interim): {stem}")
            continue
        log(f"tag: {stem}")
        try:
            tag_one(ctx, stem, tax, ep, log, cancel)
            ok.append(n)
        except Exception as e:
            failed[n] = str(e)
            log(f"  ERROR {stem}: {e}")
    log(f"tag finished: {len(ok)} ok, {len(failed)} failed {list(failed) or ''}")
    return {"ok": ok, "failed": failed}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true")
    context.add_ctx_args(ap)
    a = ap.parse_args()
    ctx = context.ctx_from_args(a)
    names = (sorted({p.name.split(".")[0] for p in ctx.interim_dir.glob("*.jsonl")}) if a.all else a.files)
    if not names:
        ap.error("give stems or --all")
    tag_files(ctx, names)
