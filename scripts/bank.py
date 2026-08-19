"""M4 bank check: validate every live entry across a subject/stage.

Loads each stem's live stage file (`interim_build.newest_stage` — tagged > clean > raw, the
same resolution the dashboard bank uses) for every source under the subject/stage, and checks
each entry against the final schema: required fields present, content in `stem` or `parts`,
topics drawn from the stage taxonomy, `qid` unique once qualified by source. Invalid rows are
reported, never silently accepted.

`collect()` is the cross-source reader the rest of the pipeline shares — `llm_gen` samples its
few-shot corpus and runs its novelty check over it.

This step used to also write a SQLite index (`db/questgen.sqlite`). Nothing ever read it: the
bank, the editor, the export and generation all go to the jsonl, so the table was write-only
and had already fallen behind the schema. The validation sweep was the part with value, so
that is all this step does now. Reintroduce an index only alongside a reader for it.

CLI: python3 scripts/bank.py [--source ...]   (or --all-sources to sweep the tree)
"""
from __future__ import annotations

from pathlib import Path

import context
import interim_build as ib
import llm_tag

# `stem` is NOT here: it may legitimately be "" when a paper starts straight at (a)
# (docs/INTERIM_SCHEMA.md §1). Such a question carries its content in `parts`, which
# the emptiness check below covers.
REQUIRED = ("qid", "kind", "meta")


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
    if not (entry.get("stem") or entry.get("parts")):
        errs.append("empty:stem+parts")
    for t in (entry.get("tags", {}).get("topic") or []):
        if t not in tax:
            errs.append(f"bad_topic:{t}")
    return errs


def check(ctx: context.Ctx, log=print) -> dict:
    """Validate every live entry across this subject/stage; report, change nothing."""
    try:
        tax = set(llm_tag.load_taxonomy(ctx))
    except FileNotFoundError:
        tax = set()
    rows = collect(ctx)
    # with no taxonomy on disk, judge topics against the ones the bank actually uses, so a
    # missing _taxonomy file does not flag every tagged entry
    all_topics = set(t for x in rows for t in (x.get("tags", {}).get("topic") or []))
    good, invalid, warned, seen = [], [], [], set()
    for r in rows:
        gid = f"{r.get('meta', {}).get('source_id', '?')}/{r['qid']}"   # qid alone collides
        if gid in seen:
            invalid.append((gid, ["duplicate"]))
            continue
        seen.add(gid)
        errs = validate(r, tax or all_topics)
        # only a structural error keeps an entry out; a bad topic is a tagging problem to
        # look at, not a reason to call the entry unusable
        if [e for e in errs if e.startswith(("missing", "empty"))]:
            invalid.append((gid, errs))
        else:
            good.append(gid)
            if errs:
                warned.append((gid, errs))
    log(f"  {ctx.subject}/{ctx.stage}: {len(good)} ok, {len(invalid)} invalid"
        + (f", {len(warned)} with warnings" if warned else ""))
    for gid, errs in (invalid + warned)[:10]:
        log(f"    {gid}: {errs}")
    return {"source": ctx.source, "ok": len(good), "invalid": len(invalid),
            "warned": len(warned), "invalid_ids": [g for g, _ in invalid]}


def check_files(ctx: context.Ctx, names=None, log=print, cancel=None) -> dict:
    """names ignored (the check is per subject/stage); dashboard step-runner signature."""
    return check(ctx, log)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--all-sources", action="store_true", help="every source in the tree")
    context.add_ctx_args(ap)
    a = ap.parse_args()
    if a.all_sources:
        root = context.content_root(a.content_root)
        for s in context.discover(root):
            check(context.Ctx(s["subject"], s["stage"], s["level"], s["source"], root))
    else:
        check(context.ctx_from_args(a))
