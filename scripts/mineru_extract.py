"""M2: MinerU cloud extraction. raw/*.pdf → extracted/<stem>/{content_list.json, middle.json, full.md, images/}

Thin HTTP client — compute happens on MinerU's cloud. Needs network + token,
so it runs on the user's machine (not the sandbox). Scans → vlm + is_ocr.
公式 $/$$ 与 HTML table 为 mineru 原生输出, 下游延用不转换 (NOTES.md)。

Token: $MINERU_TOKEN, else config/mineru_token.txt.
CLI:   python3 scripts/mineru_extract.py --all          # every un-extracted raw pdf
       python3 scripts/mineru_extract.py Chapter_1.pdf
       MINERU_MOCK=1 ... --all                          # fake outputs, plumbing test

Library (used by dashboard Pipeline tab):
       extract_files(ctx, names, log=fn, cancel=Event) -> {"ok": [...], "failed": {name: err}}
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

import context
import interim_build

BASE = "https://mineru.net/api/v4"
MODEL = {"model_version": "vlm", "enable_formula": True, "enable_table": True}
BATCH_SIZE = 10          # files per cloud batch (processed in parallel server-side)
POLL_INTERVAL = 6
POLL_TIMEOUT = 3600


class Cancelled(Exception):
    pass


def load_token() -> str:
    tok = os.environ.get("MINERU_TOKEN", "").strip()
    if tok:
        return tok
    rel = context.CONFIG.get("mineru", {}).get("token_file", "config/mineru_token.txt")
    p = context.ENGINE_ROOT / rel
    if p.is_file():
        return p.read_text(encoding="utf-8").strip()
    return ""


def is_extracted(ctx: context.Ctx, name: str) -> bool:
    d = ctx.extracted_dir / Path(name).stem
    return d.is_dir() and (any(d.glob("*content_list.json")) or any(d.iterdir()))


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled("cancelled by user")


def _headers(token):
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _submit_batch(token, paths: list[Path], log) -> str:
    import requests
    body = dict(MODEL, files=[{"name": p.name, "is_ocr": True, "data_id": p.name} for p in paths])
    r = requests.post(f"{BASE}/file-urls/batch", headers=_headers(token), json=body, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"apply-url failed: {j.get('msg')} (code {j.get('code')})")
    batch_id = j["data"]["batch_id"]
    for p, url in zip(paths, j["data"]["file_urls"]):
        log(f"  upload {p.name} ({p.stat().st_size/1e6:.1f} MB) …")
        # `data=f` streams the file in chunks; an evicted (iCloud dataless) PDF can read
        # back short, and MinerU would happily extract from nothing. Make it local first.
        interim_build.materialize(p)
        with open(p, "rb") as f:
            requests.put(url, data=f, timeout=600).raise_for_status()  # no auth on PUT
    log(f"  batch submitted: {batch_id} ({len(paths)} files)")
    return batch_id


def _poll_batch(token, batch_id, names: list[str], log, cancel) -> dict:
    """Poll until every file is done/failed. Returns {name: zip_url | Exception}."""
    import requests
    t0 = time.time()
    result: dict = {}
    while time.time() - t0 < POLL_TIMEOUT:
        _check(cancel)
        r = requests.get(f"{BASE}/extract-results/batch/{batch_id}", headers=_headers(token), timeout=60)
        r.raise_for_status()
        entries = r.json()["data"]["extract_result"]
        lines = []
        for name in names:
            if name in result:
                continue
            me = next((x for x in entries if x.get("data_id") == name or x.get("file_name") == name), None)
            if me is None:
                continue
            state = me.get("state")
            if state == "done":
                result[name] = me["full_zip_url"]
                log(f"  done: {name}")
            elif state == "failed":
                result[name] = RuntimeError(me.get("err_msg") or "extract failed")
                log(f"  FAILED: {name}: {me.get('err_msg')}")
            else:
                prog = me.get("extract_progress") or {}
                pp = f" {prog.get('extracted_pages', '?')}/{prog.get('total_pages', '?')}p" if prog else ""
                lines.append(f"{name}:{state}{pp}")
        if len(result) == len(names):
            return result
        log(f"  [{int(time.time()-t0)}s] " + "  ".join(lines))
        for _ in range(POLL_INTERVAL):
            _check(cancel)
            time.sleep(1)
    raise TimeoutError(f"batch {batch_id} timed out after {POLL_TIMEOUT}s")


def _download(ctx: context.Ctx, name: str, zip_url: str, log):
    """Download result zip, unzip to temp, copy into extracted/<stem>/ (fuse-safe)."""
    import requests
    stem = Path(name).stem
    dest = ctx.extracted_dir / stem
    tmp = Path(tempfile.mkdtemp(prefix="questgen_mineru_"))
    try:
        data = requests.get(zip_url, timeout=600).content
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmp)
        if dest.is_dir():
            shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(tmp, dest)
        n_img = len(list((dest / "images").glob("*"))) if (dest / "images").is_dir() else 0
        log(f"  -> extracted/{stem}/ ({n_img} images)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mock_extract(ctx: context.Ctx, paths: list[Path], log, cancel) -> dict:
    """MINERU_MOCK=1: fake outputs for plumbing tests. No network."""
    ok, failed = [], {}
    for p in paths:
        _check(cancel)
        log(f"  [mock] extracting {p.name} …")
        time.sleep(1)
        dest = ctx.extracted_dir / p.stem
        (dest / "images").mkdir(parents=True, exist_ok=True)
        (dest / f"{p.stem}_content_list.json").write_text(json.dumps(
            [{"type": "text", "text": f"mock block from {p.name}", "page_idx": 0}]), encoding="utf-8")
        (dest / "full.md").write_text(f"# mock\nfrom {p.name}\n$$x^2$$\n", encoding="utf-8")
        log(f"  -> extracted/{p.stem}/ (mock)")
        ok.append(p.name)
    return {"ok": ok, "failed": failed}


def extract_files(ctx: context.Ctx, names: list[str], log=print, cancel=None, force=False) -> dict:
    """Extract raw/<name> files. Skips already-extracted unless force=True (re-extract).
    Returns {"ok":[], "failed":{name: err}}."""
    paths = []
    for n in names:
        p = ctx.raw_dir / n
        if not p.is_file():
            raise FileNotFoundError(f"raw/{n}")
        if is_extracted(ctx, n) and not force:
            log(f"skip (already extracted): {n}")
            continue
        if force and is_extracted(ctx, n):
            log(f"force re-extract: {n}")
        paths.append(p)
    if not paths:
        log("nothing to do.")
        return {"ok": [], "failed": {}}
    ctx.extracted_dir.mkdir(parents=True, exist_ok=True)

    if os.environ.get("MINERU_MOCK"):
        return _mock_extract(ctx, paths, log, cancel)

    token = load_token()
    if not token:
        raise RuntimeError("no MinerU token: set $MINERU_TOKEN or create config/mineru_token.txt")

    ok, failed = [], {}
    for i in range(0, len(paths), BATCH_SIZE):
        chunk = paths[i:i + BATCH_SIZE]
        log(f"batch {i//BATCH_SIZE+1}: {len(chunk)} file(s)")
        try:
            batch_id = _submit_batch(token, chunk, log)
            results = _poll_batch(token, batch_id, [p.name for p in chunk], log, cancel)
        except Cancelled:
            raise
        except Exception as e:
            for p in chunk:
                failed[p.name] = str(e)
            log(f"  batch ERROR: {e}")
            continue
        for p in chunk:
            r = results.get(p.name)
            if isinstance(r, str):
                try:
                    _download(ctx, p.name, r, log)
                    ok.append(p.name)
                except Exception as e:
                    failed[p.name] = str(e)
                    log(f"  download ERROR {p.name}: {e}")
            else:
                failed[p.name] = str(r)
    log(f"extract finished: {len(ok)} ok, {len(failed)} failed {list(failed) or ''}")
    return {"ok": ok, "failed": failed}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*", help="filenames under raw/, or --all")
    ap.add_argument("--all", action="store_true")
    context.add_ctx_args(ap)
    a = ap.parse_args()
    ctx = context.ctx_from_args(a)
    names = (sorted(p.name for p in ctx.raw_dir.glob("*.pdf")) if a.all
             else a.files)
    if not names:
        ap.error("give filenames or --all")
    extract_files(ctx, names)
