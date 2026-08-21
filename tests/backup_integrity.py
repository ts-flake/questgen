#!/usr/bin/env python3
"""Regression tests for backup verification and atomic restore.

Both properties fail silently when they regress, which is why they are pinned here:

  * a backup was written and never read again, so a corrupt archive sat in the list looking
    healthy and was only discovered at restore time — exactly when the original is gone;
  * restore copied member by member straight out of the archive, so a member that failed to
    decompress left the earlier ones already written and interim/ became a silent mix of two
    points in time, while the caller saw only an exception it would read as "nothing happened".

    python3 tests/backup_integrity.py

Builds its own tiny content tree in a temp dir: no fixtures, no network, no real content.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import context  # noqa: E402
import dashboard  # noqa: E402
import interim_build as ib  # noqa: E402

STEMS = ("alpha", "beta", "gamma", "delta")


def _tree() -> tuple[Path, context.Ctx]:
    root = Path(tempfile.mkdtemp(prefix="qg_bak_"))
    ctx = context.Ctx("s", "t", "l", "src", root)
    ctx.interim_dir.mkdir(parents=True, exist_ok=True)
    for k, stem in enumerate(STEMS):
        rows = [{"qid": f"{stem}-001", "kind": "question", "stem": f"original {stem}",
                 "parts": [], "options": None, "answer": None, "solution": None,
                 "imgs": [], "flags": [], "meta": {"file": f"{stem}.pdf", "pages": [k]}}]
        (ctx.interim_dir / f"{stem}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return root, ctx


def _corrupt_member(zip_path: Path, member: str) -> None:
    """Damage one member's deflate stream, leaving every zip header intact."""
    with zipfile.ZipFile(zip_path) as zf:
        info = zf.getinfo(member)
    data = bytearray(zip_path.read_bytes())
    start = info.header_offset + 30 + len(info.filename) + len(info.extra)
    for i in range(start + 8, start + 40):
        data[i] ^= 0xFF
    zip_path.write_bytes(bytes(data))


def _stem_of(ctx, name: str) -> str:
    return ib.read_jsonl(ctx.interim_dir / name, required=False)[0]["stem"]


def check(label: str, cond: bool) -> int:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    return 0 if cond else 1


def main() -> int:
    fails = 0

    # --- a healthy backup round-trips byte for byte -------------------------
    root, ctx = _tree()
    try:
        before = {p.name: p.read_bytes() for p in ctx.interim_dir.iterdir()}
        name = dashboard.backup_db(ctx, tag="t")["file"]
        (ctx.interim_dir / "alpha.jsonl").write_text('{"qid":"x","stem":"MUTATED"}\n')
        dashboard.restore_db(ctx, {"file": name})
        after = {p.name: p.read_bytes() for p in ctx.interim_dir.iterdir()
                 if not p.name.endswith(".superseded")}
        fails += check("healthy backup restores byte-identical",
                       all(after.get(k) == v for k, v in before.items()))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # --- a corrupt archive is refused, and interim/ is left ALONE -----------
    root, ctx = _tree()
    try:
        name = dashboard.backup_db(ctx, tag="t")["file"]
        zip_path = dashboard._backups_dir(ctx) / name
        # edit two files that sort on either side of the member we damage, so a member-by-
        # member restore would roll back the first and not the second
        for stem in ("alpha", "gamma"):
            (ctx.interim_dir / f"{stem}.jsonl").write_text(
                json.dumps({"qid": f"{stem}-001", "stem": "EDITED"}) + "\n", encoding="utf-8")
        _corrupt_member(zip_path, "beta.jsonl")
        n_backups = len(list(dashboard._backups_dir(ctx).glob("*.zip")))

        refused = False
        try:
            dashboard.restore_db(ctx, {"file": name})
        except Exception:
            refused = True
        fails += check("corrupt archive: restore refuses", refused)
        fails += check("corrupt archive: no half-restored state",
                       _stem_of(ctx, "alpha.jsonl") == "EDITED"
                       and _stem_of(ctx, "gamma.jsonl") == "EDITED")
        fails += check("corrupt archive: the pre-restore snapshot was still taken",
                       len(list(dashboard._backups_dir(ctx).glob("*.zip"))) > n_backups)

        bad = False
        try:
            dashboard._verify_zip(zip_path)
        except Exception:
            bad = True
        fails += check("corrupt archive: _verify_zip rejects it", bad)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # --- a backup that cannot be verified is not left behind ----------------
    root, ctx = _tree()
    try:
        real_copy = shutil.copy2

        def corrupting_copy(src, dst, *a, **k):
            out = real_copy(src, dst, *a, **k)
            data = bytearray(Path(out).read_bytes())
            for i in range(200, 260):
                data[i] ^= 0xFF
            Path(out).write_bytes(bytes(data))
            return out

        dashboard.shutil.copy2 = corrupting_copy
        raised = False
        try:
            dashboard.backup_db(ctx, tag="willfail")
        except Exception:
            raised = True
        finally:
            dashboard.shutil.copy2 = real_copy
        fails += check("bad write: backup_db raises", raised)
        fails += check("bad write: no unverified zip left on disk",
                       not list(dashboard._backups_dir(ctx).glob("*willfail*")))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{'FAILURES: ' + str(fails) if fails else 'all passed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
