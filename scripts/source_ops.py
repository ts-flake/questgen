"""M1 executor: page range select / crop / mask / merge on source PDFs.

Every save writes the output PDF to raw/ AND records the full plan in raw/ops.json,
so raw/ is always reproducible from original/ (`python3 scripts/source_ops.py --replay`).

Plan format (also the ops.json entry format):
{
  "output": "Chapter_1.pdf",
  "steps": [
    { "source": "original/Book.pdf",          # path relative to the source dir
      "pages": [6, 7, 8],                     # 1-based, in output order
      "edits": {                              # optional, keyed by 1-based page no
        "6": { "crop":  [x0, y0, x1, y1],     # normalized 0-1, origin top-left
               "masks": [[x0, y0, x1, y1]] }  # white boxes, same coords
      } }
  ]
}
Multiple steps = merge. Coordinates are relative to the page as displayed
(page.rect), so they match what the user drew in the dashboard.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

import fitz  # PyMuPDF

OPS_VERSION = 1


def parse_pages(spec, page_count: int) -> list[int]:
    """'6-40,55' or [6,7] -> validated 1-based list."""
    if isinstance(spec, list):
        pages = [int(p) for p in spec]
    else:
        pages = []
        for part in str(spec).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                pages.extend(range(int(a), int(b) + 1))
            else:
                pages.append(int(part))
    bad = [p for p in pages if p < 1 or p > page_count]
    if bad:
        raise ValueError(f"pages out of range 1..{page_count}: {bad}")
    return pages


def _norm_rect(page: fitz.Page, r) -> fitz.Rect:
    """Normalized (0-1, top-left origin, display space) -> unrotated page coords."""
    w, h = page.rect.width, page.rect.height
    disp = fitz.Rect(r[0] * w, r[1] * h, r[2] * w, r[3] * h)
    return (disp * page.derotation_matrix).normalize()


def _apply_step(out: fitz.Document, source_dir: Path, step: dict) -> None:
    src_path = (source_dir / step["source"]).resolve()
    if not src_path.is_file():
        raise FileNotFoundError(str(src_path))
    doc = fitz.open(src_path)
    pages = parse_pages(step["pages"], doc.page_count)
    edits = step.get("edits") or {}
    for pno in sorted(set(pages)):
        ed = edits.get(str(pno)) or edits.get(pno)
        if not ed:
            continue
        page = doc[pno - 1]
        for m in ed.get("masks") or []:
            page.draw_rect(_norm_rect(page, m), color=None, fill=(1, 1, 1), overlay=True)
        if ed.get("crop"):
            rect = _norm_rect(page, ed["crop"])
            mb = page.mediabox
            rect = fitz.Rect(rect.x0 + mb.x0, rect.y0 + mb.y0, rect.x1 + mb.x0, rect.y1 + mb.y0) & mb
            page.set_cropbox(rect)
    doc.select([p - 1 for p in pages])
    out.insert_pdf(doc)
    doc.close()


def load_ops(source_dir: Path) -> dict:
    p = source_dir / "raw" / "ops.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"version": OPS_VERSION, "outputs": {}}


def apply_plan(source_dir: Path, plan: dict) -> Path:
    """Execute plan, write raw/<output> (temp then copy: fuse), record in ops.json."""
    name = plan["output"]
    if "/" in name or not name.lower().endswith(".pdf"):
        raise ValueError(f"bad output name: {name}")
    raw_dir = source_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="questgen_ops_"))
    try:
        out = fitz.open()
        for step in plan["steps"]:
            _apply_step(out, source_dir, step)
        if out.page_count == 0:
            raise ValueError("plan produced 0 pages")
        tmp_pdf = tmp / name
        out.save(tmp_pdf, garbage=3, deflate=True)
        out.close()
        shutil.copy2(tmp_pdf, raw_dir / name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ops = load_ops(source_dir)
    ops["version"] = OPS_VERSION
    ops["outputs"][name] = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "steps": plan["steps"]}
    (raw_dir / "ops.json").write_text(
        json.dumps(ops, ensure_ascii=False, indent=1), encoding="utf-8")
    return raw_dir / name


def page_map(source_dir: Path, output_name: str) -> list[dict]:
    """Output page (1-based) -> provenance chain back to original/.
    Returns [{page, source, source_page, crop}]; recurses through raw/-sourced steps."""
    ops = load_ops(source_dir)["outputs"]

    def resolve(name: str) -> list[dict]:
        entry = ops.get(name)
        if entry is None:  # not ops-produced (e.g. dropped directly into raw/)
            return []
        rows = []
        for step in entry["steps"]:
            src = step["source"]
            edits = step.get("edits") or {}
            inner = resolve(Path(src).name) if src.startswith("raw/") else None
            for pno in step["pages"]:
                ed = edits.get(str(pno)) or {}
                if inner:  # chain through the intermediate raw file
                    base = dict(inner[pno - 1])
                    if ed.get("crop"):
                        base["chain_crop"] = base.get("chain_crop", []) + [ed["crop"]]
                    rows.append(base)
                else:
                    rows.append({"source": src, "source_page": pno,
                                 "crop": ed.get("crop")})
        return rows

    rows = resolve(output_name)
    return [{"page": i + 1, **r} for i, r in enumerate(rows)]


def replay(source_dir: Path) -> list[str]:
    """Rebuild every ops.json output from scratch. Proof of reproducibility."""
    done = []
    for name, entry in load_ops(source_dir)["outputs"].items():
        apply_plan(source_dir, {"output": name, "steps": entry["steps"]})
        done.append(name)
    return done


if __name__ == "__main__":
    import context
    ap = argparse.ArgumentParser(description=__doc__)
    context.add_ctx_args(ap)
    ap.add_argument("--replay", action="store_true", help="rebuild raw/ from ops.json")
    ap.add_argument("--plan", help="path to a plan json to apply")
    ap.add_argument("--map", metavar="OUTPUT", help="print page_map for a raw output")
    a = ap.parse_args()
    ctx = context.ctx_from_args(a)
    if a.replay:
        for n in replay(ctx.source_dir):
            print("rebuilt", n)
    elif a.plan:
        print(apply_plan(ctx.source_dir, json.loads(Path(a.plan).read_text())))
    elif a.map:
        print(json.dumps(page_map(ctx.source_dir, a.map), indent=1))
    else:
        ap.print_help()
