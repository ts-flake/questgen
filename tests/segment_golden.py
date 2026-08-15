#!/usr/bin/env python3
"""Golden tests for the segmentation → assembly step.

A fixture freezes ONE real MinerU extraction plus the LLM's block labels, so the whole
question-assembly path can be replayed offline: no endpoint, no network, no content tree.
Refactors of `assemble_questions` (and later the table pass) are then checked against a
known-good output instead of an ad-hoc script.

    python3 tests/segment_golden.py                 # run every fixture
    python3 tests/segment_golden.py -k p2b          # only fixtures matching a substring
    python3 tests/segment_golden.py --update        # re-bless expectations (review the diff!)
    python3 tests/segment_golden.py --add NAME \\
        --subject chemistry --stage secondary --level olvl --source paper_25 --stem chij_p1

`--add` imports from a real content tree: it copies that stem's `*content_list.json` and the
labels already cached in `interim/<stem>.segment.log.json`, so no LLM call is made.

Fixtures hold real exam text and are gitignored by default (see tests/.gitignore).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import context  # noqa: E402
import interim_build as ib  # noqa: E402
import llm_segment as ls  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPECTED = Path(__file__).resolve().parent / "expected"


def _materialise(fx: dict, root: Path) -> tuple[context.Ctx, str]:
    """Write a fixture back out as a minimal content tree so the REAL loader runs against it
    (block loading does noise filtering / rescue / unknown-type handling — worth covering)."""
    stem = fx["stem"]
    ctx = context.Ctx("fx", "fx", "fx", fx.get("name", "case"), root)
    d = ctx.extracted_dir / stem
    d.mkdir(parents=True, exist_ok=True)
    (d / "content_list.json").write_text(
        json.dumps(fx["content_list"], ensure_ascii=False), encoding="utf-8")
    return ctx, stem


def run_fixture(fx: dict) -> list[dict]:
    """Replay one fixture through load_blocks + assemble_questions."""
    tmp = Path(tempfile.mkdtemp(prefix="qg_golden_"))
    try:
        ctx, stem = _materialise(fx, tmp)
        blocks = ib.load_blocks(ctx, stem)
        labels = {int(k): v for k, v in fx["labels"].items()}
        return _normalise(ls.assemble_questions(blocks, labels))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _normalise(entries: list[dict]) -> list[dict]:
    """Make the assembler output comparable/diffable: sets → sorted lists, stable key order."""
    out = []
    for e in entries:
        e = dict(e)
        if isinstance(e.get("pages"), set):
            e["pages"] = sorted(e["pages"])
        out.append(json.loads(json.dumps(e, ensure_ascii=False, sort_keys=True)))
    return out


def _fixtures(pattern: str | None) -> list[Path]:
    if not FIXTURES.is_dir():
        return []
    return [p for p in sorted(FIXTURES.glob("*.json"))
            if not pattern or pattern in p.stem]


def cmd_add(a) -> int:
    ctx = context.Ctx(a.subject, a.stage, a.level, a.source)
    cl = ib.content_list_path(ctx, a.stem)
    if not cl:
        print(f"no content_list for {a.stem} in {ctx.extracted_dir}", file=sys.stderr)
        return 2
    log = ctx.interim_dir / f"{a.stem}.segment.log.json"
    if not log.is_file():
        print(f"no cached labels: {log}\n(run the interim step once so labels are logged)",
              file=sys.stderr)
        return 2
    labels = json.loads(log.read_text(encoding="utf-8")).get("question_labels") or {}
    if not labels:
        print(f"{log} has no question_labels", file=sys.stderr)
        return 2
    FIXTURES.mkdir(parents=True, exist_ok=True)
    fx = {"name": a.name, "stem": a.stem,
          "origin": f"{a.subject}/{a.stage}/{a.level}/{a.source}",
          "labels": labels,
          "content_list": json.loads(Path(cl).read_text(encoding="utf-8"))}
    (FIXTURES / f"{a.name}.json").write_text(
        json.dumps(fx, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"added fixture {a.name}: {len(fx['content_list'])} blocks, {len(labels)} labels")
    print("now bless the expectation:  python3 tests/segment_golden.py --update -k " + a.name)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--update", action="store_true", help="re-bless expectations")
    ap.add_argument("-k", dest="pattern", default=None, help="only fixtures matching this")
    ap.add_argument("--add", dest="name", help="import a new fixture from a content tree")
    for opt in ("subject", "stage", "level", "source", "stem"):
        ap.add_argument(f"--{opt}")
    a = ap.parse_args()

    if a.name:
        return cmd_add(a)

    files = _fixtures(a.pattern)
    if not files:
        print(f"no fixtures in {FIXTURES} — add one with --add (see --help)")
        return 0

    EXPECTED.mkdir(parents=True, exist_ok=True)
    fails = 0
    for f in files:
        fx = json.loads(f.read_text(encoding="utf-8"))
        got = run_fixture(fx)
        exp_path = EXPECTED / f.name
        if a.update or not exp_path.is_file():
            exp_path.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  BLESSED {f.stem}: {len(got)} entries")
            continue
        exp = json.loads(exp_path.read_text(encoding="utf-8"))
        if got == exp:
            print(f"  ok      {f.stem}: {len(got)} entries")
        else:
            fails += 1
            print(f"  FAIL    {f.stem}: {len(exp)} expected vs {len(got)} got")
            for line in _diff(exp, got):
                print("            " + line)
    if fails:
        print(f"\n{fails} fixture(s) differ. Inspect, then --update if the change is intended.")
    return 1 if fails else 0


def _diff(exp: list[dict], got: list[dict], limit: int = 12) -> list[str]:
    """Human-readable first differences (field level, not raw json)."""
    out = []
    for i in range(max(len(exp), len(got))):
        if i >= len(exp):
            out.append(f"[{i}] extra entry qno={got[i].get('qno')}")
        elif i >= len(got):
            out.append(f"[{i}] missing entry qno={exp[i].get('qno')}")
        elif exp[i] != got[i]:
            for k in sorted(set(exp[i]) | set(got[i])):
                if exp[i].get(k) != got[i].get(k):
                    out.append(f"[{i}].{k}: {_short(exp[i].get(k))} -> {_short(got[i].get(k))}")
        if len(out) >= limit:
            out.append("…")
            break
    return out


def _short(v, n: int = 70) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else repr(v)
    return s if len(s) <= n else s[:n] + "…"


if __name__ == "__main__":
    sys.exit(main())
