"""M3 Stage-2: LLM clean over interim/*.jsonl → interim/*.clean.jsonl

Segmentation is high-recall (faithful, never drops, may merge a colliding qno's
solution). This pass adds PRECISION per entry:
  - repair MinerU/OCR errors in stem/parts/solution (reorganize, fix symbols/latex);
  - PRUNE a merged solution to only this question (section-restart collisions);
  - FILL a missing answer from the solution; never fabricate a missing solution;
  - preserve option labels and ![]() figure markers;
  - forced per-field verdicts; severity ok|fixed|severe (severe -> human review).
A minimal lint guard rejects only structural garbage; other issues become flags.

DEFAULT = text LLM (fast). The model can't see figures, so figure safety is
enforced deterministically regardless of endpoint: _restore_markers re-appends any
![]() marker the LLM drops; norm_symbols normalizes <img>/\\cent. --vlm opts into a
vision model that also reads rendered source pages for figure questions (accurate
but ~1-2 min per figure question — use selectively).

Endpoint: `llm:` text by default; `vlm:` vision when --vlm. interim/*.jsonl never
modified; audit log *.clean.log.json. QUESTGEN_LLM_MOCK=1 = offline plumbing test.

CLI: python3 scripts/llm_clean.py --all [--flagged-only] [--limit N] [--vlm]
"""
from __future__ import annotations

import base64
import glob
import json
import os
import re
import time
from pathlib import Path

import context

CFG = context.CONFIG.get("llm", {})
VCFG = context.CONFIG.get("vlm", {})
GCFG = context.CONFIG.get("gen", {})


def reload_cfg() -> None:
    """Re-read config sections after the dashboard edits config.yaml (hot-reload)."""
    global CFG, VCFG, GCFG
    context.reload_config()
    CFG = context.CONFIG.get("llm", {})
    VCFG = context.CONFIG.get("vlm", {})
    GCFG = context.CONFIG.get("gen", {})

# ---------------------------------------------------------------- prompts
# Style follows JudgePeach/math-question-bank: numbered rules, positive/negative
# examples, hard output-format constraints, no chatter.

SYS_T1 = """You are a data-quality editor for an exam question bank extracted from a scanned paper.
You get SOURCE TEXT (machine-parsed, may contain OCR damage and scrambled two-column reading order),
ONE extracted entry (json), and — for questions with figures — IMAGES of the original scanned page(s).
Review EVERY field and repair it.

[GROUND TRUTH]
When page IMAGES are attached, they are the authoritative source: read figures, graphs, option
formulas printed as pictures, and answer keys directly from them. The machine-parsed SOURCE TEXT can
miss or garble anything visual — trust the image over the text when they disagree. When no image is
attached, use the SOURCE TEXT.

[REPAIR AUTHORITY]
You MAY reorganize broken solution lines, merge fragments split by column layout, fix OCR symbol
errors (e.g. a cents sign misread as \\phi — write the unicode ¢, never \\cent or \\phi), and repair
latex — as long as the content stays faithful to the source. You MUST NOT invent content.

[RULES — each numbered rule is mandatory]
1. Per-field verdict (VERY IMPORTANT): output a verdict for ALL five fields — stem, parts, options,
   answer, solution — each one of "ok" | "fixed" | "na" (na = field not applicable, e.g. no options).
   Skipping a field is not allowed.
2. stem must NEVER begin with the question number.
   Wrong: "15. Janice spent $6w every day..."   Right: "Janice spent $6w every day..."
3. Latex hygiene: every math fragment must be wrapped in $...$ (inline) or $$...$$ (display),
   and every $ / { must be balanced — count them before you answer.
   Wrong: "135^{\\circ}"   Right: "$135^{\\circ}$"
   Wrong: "$12.50 = 1250¢"  (unbalanced $)   Right: "\\$12.50 = 1250¢"
   Never wrap plain English sentences in latex; never use \\text{...} for normal sentences;
   plain numbers and words need no wrapping. Keep HTML <table> as-is. Use ¢ (unicode), not \\cent.
4. Units inside math are UPRIGHT: a unit symbol is not a variable, so wrap it in \\mathrm and keep
   the thin space before it. Collapse the braces on a simple exponent.
   Wrong: "$0.25 \\, dm^{3}$"   Right: "$0.25\\,\\mathrm{dm^3}$"
   Wrong: "$9.8 m s^{-2}$"      Right: "$9.8\\,\\mathrm{m\\,s^{-2}}$"
   Wrong: "$25 cm^{2}$"         Right: "$25\\,\\mathrm{cm^2}$"
   The unit is often left OUTSIDE the math with only its exponent inside — pull it in:
   Wrong: "area is 25 cm $^{2}$"  Right: "area is $25\\,\\mathrm{cm^2}$"
   Wrong: "12 cm$^{3}$"           Right: "$12\\,\\mathrm{cm^3}$"
   This is only for UNITS (m, cm, kg, s, N, J, mol, dm^3, °C ...). Algebraic variables stay italic:
   "$x^{2}$", "$v = u + at$" are already correct — never wrap those in \\mathrm.
5. Escaped currency: dollar amounts in text stay as \\$ (e.g. \\$150); they are NOT math delimiters.
6. Answer-blank placeholder: a fill-in-the-blank for the student's answer. It appears as underscores
   ("Ans: ____", "$____", "____cm") OR dot-leaders (exam style: "v = ........ m", "……"). KEEP it (it
   drives the docx exam layout) but normalize the blank run to the token [ANSWER]. Exam answer lines
   are usually "<symbol> = [ANSWER] <unit>" — preserve the symbol, "=", and unit in place. Never
   delete an existing [ANSWER].
   Right: "Ans: [ANSWER] kg"  ·  "$v =$ [ANSWER] m s^{-1}"  ·  "area = [ANSWER] cm^2"
FIG. Figure/table references "Fig N.X" / "Figure N.X" / "Table N.X" in the text: keep them but write
   "figure [QN].X" / "table [QN].X" — replace the source's leading number N with the literal token
   [QN] (a placeholder for this question's number), keep X. Never delete [QN] once present.
7. options: keys are the option labels IN THE PAPER'S ORDER, written "(1)","(2)","(3)","(4)" — the
   bank's internal convention (a paper printed A/B/C/D becomes (1)(2)(3)(4) in the same order; export
   re-renders A./B. later). Never reorder or drop an option. Values carry no label prefix and no empty
   brackets "( )". If an option's value is only a figure, keep its ![](...) marker as the value.
IMG. NEVER delete or move a ![](...) image marker — each references a real extracted figure. You may
   only keep them. (A marker missing from the SOURCE TEXT does not mean the figure is absent.)
PARTS. Destructure sub-parts fully. Shared context goes in "stem"; EACH labelled sub-part is its OWN
   entry in the flat "parts" list as {"no":"...","text":"..."}, in reading order. Do NOT leave a
   leading "(a) ..." inside the stem; do NOT lump several sub-parts into one. For a sub-part nested
   under a parent, give its FULL path in "no" (e.g. "(a)(i)","(a)(ii)","(b)") — the pipeline turns
   these into a nested tree with local labels automatically. If the source clearly has sub-parts that
   were merged, split them.
8. answer: {"value": ..., "kind": ...}. If you fix the value, set kind to "llm". For an MCQ the value
   is the chosen option label, e.g. {"value": "(3)", "kind": "llm"} (multi-answer: "(2) (3)").
   For a multi-part question capture EVERY part's
   answer, labelled: {"value": "(a) $5$; (b) $12$", "kind": "llm"}.
9. PRUNE the solution to THIS question only. The extractor is high-recall and may have merged another
   question's solution into this one (numbering restarts across sections, so two 'Q7' solutions can be
   concatenated). Compare the solution against THIS question's stem/parts; DELETE any working that
   solves a different problem. Keep only what belongs here.
10. FILL a missing answer FROM the solution: if solution is present but answer is null/empty, read the
   solution's final result and set the answer. Do NOT do the reverse — if answer is present but
   solution is null, NEVER fabricate a solution; leave it null.
11. severity: "ok" (nothing changed) | "fixed" (you repaired something) | "severe" (unresolvable even
   with the image — required content simply not present anywhere). Severe entries go to human review —
   do NOT guess; say in "reason" what is needed.

[OUTPUT — exactly ONE json object, no other text]
{"fields": {"stem":"ok|fixed","parts":"ok|fixed|na","options":"ok|fixed|na","answer":"ok|fixed|na","solution":"ok|fixed|na"},
 "patch": { only the fixed fields, with their FULL new values (set answer/solution to null to clear) },
 "severity": "ok|fixed|severe",
 "reason": "short; required when severity is fixed or severe"}"""

# Appended to the system prompt in chemistry mode (pipeline "化学内容" option). MinerU emits
# plain-latex chemistry (H_{2}O, \mathrm{SO}_3, \rightarrow, states); convert to mhchem \ce{}.
CHEM_RULE = r"""

[CHEMISTRY MODE — this paper is chemistry] Rewrite EVERY chemical formula, species and
equation as mhchem \ce{...} inside math ($...$). Convert the plain-latex chemistry MinerU
produced; keep the chemical meaning identical.
- Formula: "$H_{2}O$" -> "$\ce{H2O}$" ; "$\mathrm{SO_4^{2-}}$" -> "$\ce{SO4^2-}$" ; "$\mathrm{Ca(OH)_2}$" -> "$\ce{Ca(OH)2}$"
- Equation (put ONE \ce around the whole thing): "$2\mathrm{SO}_2(g) + O_2(g) \rightarrow 2\mathrm{SO}_3(g)$"
  -> "$\ce{2SO2(g) + O2(g) -> 2SO3(g)}$"
- Arrows inside \ce: \rightarrow => ->, \rightleftharpoons or ⇌ => <=>, \leftarrow => <-. Keep + between species.
- State symbols (g)(l)(s)(aq) stay. Keep subscripts as digits (H2O, not H_2O is fine inside \ce).
- Do NOT put plain math (numbers, algebra, physics units) inside \ce — only chemistry."""

# SYS_T2 (image repair) and question-recovery prompts live in M3.2 (VLM pass).


# ---------------------------------------------------------------- llm client

def _resolve_key(cfg: dict, default_file: str) -> str:
    key = cfg.get("api_key", "")
    if not key:
        kf = context.ENGINE_ROOT / cfg.get("key_file", default_file)
        if kf.is_file():
            key = kf.read_text(encoding="utf-8").strip()
    return key


def _think_body(base: str, thinking: bool) -> dict:
    """Reasoning/thinking is slow and both DeepSeek V4 and Qwen3.x default it ON.
    Disable it by default with the provider-appropriate param (sent per base_url so
    an unknown param isn't pushed to the wrong provider)."""
    if thinking:
        return {}
    b = base.lower()
    if "deepseek" in b:
        return {"thinking": {"type": "disabled"}}
    if "dashscope" in b or "aliyun" in b or "qwen" in b:
        return {"enable_thinking": False}
    return {}


def endpoint(thinking: bool | None = None) -> dict | None:
    """Text endpoint from config `llm:` (env MODEL_* override). thinking=None uses
    the config default; pass True/False to override per run."""
    base = os.environ.get("MODEL_BASE_URL") or CFG.get("base_url", "")
    model = os.environ.get("MODEL_NAME") or CFG.get("model", "")
    key = os.environ.get("MODEL_API_KEY") or _resolve_key(CFG, "config/llm_key.txt")
    if not base or not model:
        return None
    local = "localhost" in base or "127.0.0.1" in base
    if not key and not local:
        return None
    think = CFG.get("thinking", False) if thinking is None else thinking
    return {"base": base.rstrip("/"), "model": model, "key": key,
            "temperature": CFG.get("temperature", 0.0), "vision": False,
            "extra_body": _think_body(base, think)}


def vlm_endpoint(thinking: bool | None = None) -> dict | None:
    """Vision endpoint from config `vlm:` (own base/model/key; key falls back to the
    llm key if unset). Reads page images. thinking overrides config when passed."""
    base = VCFG.get("base_url", "") or CFG.get("base_url", "")
    model = VCFG.get("model", "")
    if not base or not model:
        return None
    key = _resolve_key(VCFG, "config/vlm_key.txt") or _resolve_key(CFG, "config/llm_key.txt")
    local = "localhost" in base or "127.0.0.1" in base
    if not key and not local:
        return None
    think = VCFG.get("thinking", False) if thinking is None else thinking
    return {"base": base.rstrip("/"), "model": model, "key": key,
            "temperature": VCFG.get("temperature", 0.0), "dpi": VCFG.get("dpi", 130),
            "vision": True, "extra_body": _think_body(base, think)}


def gen_endpoint(thinking: bool | None = None) -> dict | None:
    """Generation endpoint from config `gen:` (M5 AI question generation). Own base/model/
    key; base/key fall back to the llm config when unset. Higher default temperature."""
    base = GCFG.get("base_url", "") or CFG.get("base_url", "")
    model = GCFG.get("model", "")
    if not base or not model:
        return None
    key = _resolve_key(GCFG, "config/gen_key.txt") or _resolve_key(CFG, "config/llm_key.txt")
    local = "localhost" in base or "127.0.0.1" in base
    if not key and not local:
        return None
    think = GCFG.get("thinking", False) if thinking is None else thinking
    return {"base": base.rstrip("/"), "model": model, "key": key,
            "temperature": GCFG.get("temperature", 0.7), "vision": False,
            "max_tokens": GCFG.get("max_tokens", 8000),   # long enough for n questions + solutions
            "extra_body": _think_body(base, think)}


def chat(ep: dict, system: str, user: str, images: list[bytes] | None = None,
         retries: int = 2) -> dict | None:
    """One chat call (optionally multimodal) → parsed json object (or None)."""
    if os.environ.get("QUESTGEN_LLM_MOCK"):
        if "found" in user[:200] or "missed question" in user[:200]:
            return {"found": False}
        return {"fields": {k: "ok" for k in ("stem", "parts", "options", "answer", "solution")},
                "severity": "ok"}
    import requests
    if images:
        content = [{"type": "text", "text": user}] + [
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b).decode()}}
            for b in images]
    else:
        content = user
    body = {"model": ep["model"], "temperature": ep.get("temperature", 0.0),
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": content}]}
    if ep.get("max_tokens"):                       # avoid provider default truncating long output
        body["max_tokens"] = ep["max_tokens"]
    body.update(ep.get("extra_body") or {})       # e.g. disable thinking (provider-specific)
    headers = {"Content-Type": "application/json"}
    if ep["key"]:
        headers["Authorization"] = f"Bearer {ep['key']}"
    for _ in range(retries + 1):
        try:
            r = requests.post(f"{ep['base']}/chat/completions", json=body,
                              headers=headers, timeout=300)
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"]
            txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S)
            parsed = _parse_json(txt)
            if parsed is not None:
                return parsed
        except Exception:
            time.sleep(2)
    return None


def chat_text(ep: dict, system: str, user: str, retries: int = 2) -> str | None:
    """One chat call returning the RAW text reply (no JSON parsing) — for freeform edits
    like the dashboard's AI-assist, where the output is LaTeX/text that goes straight back
    into a field."""
    if os.environ.get("QUESTGEN_LLM_MOCK"):
        return "[MOCK] " + user[-200:]
    import requests
    body = {"model": ep["model"], "temperature": ep.get("temperature", 0.0),
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": user}]}
    if ep.get("max_tokens"):
        body["max_tokens"] = ep["max_tokens"]
    body.update(ep.get("extra_body") or {})
    headers = {"Content-Type": "application/json"}
    if ep["key"]:
        headers["Authorization"] = f"Bearer {ep['key']}"
    for _ in range(retries + 1):
        try:
            r = requests.post(f"{ep['base']}/chat/completions", json=body,
                              headers=headers, timeout=300)
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"]
            return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
        except Exception:
            time.sleep(2)
    return None


# A backslash that JSON does not recognise as an escape introducer. The payload is
# LaTeX-heavy, and the model intermittently writes "$\ce{C16H34}$" where JSON needs
# "$\\ce{...}$" — one unescaped backslash makes the WHOLE reply unparseable. Matching
# valid escapes first means an already-correct "\\ce" is consumed as a unit and left
# alone; only a lone backslash is doubled.
_ESCAPE = re.compile(r'\\(?:u[0-9a-fA-F]{4}|["\\/bfnrt])|\\')


def _repair_escapes(txt: str) -> str:
    return _ESCAPE.sub(lambda m: m.group(0) if len(m.group(0)) > 1 else "\\\\", txt)


def _parse_json(txt: str):
    """Extract a JSON value (object OR array) from a model reply, tolerant of
    ```json fences and surrounding prose.

    The fallback decodes the OUTERMOST value only — from the first bracket, with
    raw_decode so trailing prose is ignored. It never reaches inside a value it
    could not parse: a clean reply is {"fields":…,"patch":{"parts":[…]}}, and the
    old array-first regex turned any object that failed the strict parse into its
    bare parts list, which the caller then read as a patch. A payload we cannot
    decode is an honest None (the caller retries, then records an llm_error)."""
    txt = re.sub(r"```(?:json)?|```", "", txt).strip()
    for s in (txt, _repair_escapes(txt)):
        try:
            return json.loads(s)
        except Exception:
            pass
        m = re.search(r"[\[{]", s)
        if m:
            try:
                return json.JSONDecoder().raw_decode(s, m.start())[0]
            except ValueError:
                pass
    return None


# ---------------------------------------------------------------- context text & images

def page_texts(ctx: context.Ctx, stem: str) -> dict[int, str]:
    import interim_build as ib
    p = ib.content_list_path(ctx, stem)   # single MinerU-layout resolver (adapter)
    if p is None:
        return {}
    blocks = json.loads(p.read_text(encoding="utf-8"))
    pages: dict[int, list] = {}
    for b in blocks:
        if b["type"] in ("page_number", "footer", "page_footnote"):
            continue
        t = ib.block_text(b)
        if t:
            pages.setdefault(b["page_idx"], []).append(t)
    return {p: "\n\n".join(v) for p, v in pages.items()}


def slice_for(entry: dict, pages: dict[int, str]) -> str:
    cap = CFG.get("max_context_chars", 6000)
    ps = entry["meta"]["pages"]
    ps = sorted(set(ps + [p + 1 for p in ps]))
    return "\n\n".join(pages.get(p, "") for p in ps)[:cap]


FIG_WORDS = re.compile(r"\b(graph|figure|fig\.|diagram|shown below|as shown|the table below|the "
                       r"diagram|the graph|net|nets|below shows)\b", re.I)


def needs_images(entry: dict) -> bool:
    """A figure question: has an extracted figure asset, or references one in text."""
    if entry.get("imgs"):
        return True
    txt = entry["stem"] + " ".join(p.get("text", "") for p in entry.get("parts", [])) \
        + " ".join((entry.get("options") or {}).values())
    return bool(FIG_WORDS.search(txt))


def render_pages(pdf_path: Path, page_idxs: list[int], dpi: int) -> list[bytes]:
    try:
        import fitz
    except Exception:
        return []
    if not pdf_path.is_file():
        return []
    out = []
    with fitz.open(pdf_path) as doc:
        for p in sorted(set(page_idxs)):
            if 0 <= p < doc.page_count:
                out.append(doc[p].get_pixmap(dpi=dpi).tobytes("jpeg"))
    return out


def entry_images(ctx: context.Ctx, stem: str, entry: dict, dpi: int,
                 max_q=3, max_a=2) -> list[bytes]:
    """Render the source page(s) for a figure-bearing entry: question pages from
    raw/<stem>.pdf, answer-figure pages from raw/<stem>_ans.pdf."""
    q_pages = sorted({a["page"] for a in entry["imgs"] if a.get("src") != "ans" and "page" in a})
    a_pages = sorted({a["page"] for a in entry["imgs"] if a.get("src") == "ans" and "page" in a})
    if not q_pages:                                  # figure referenced but no asset
        q_pages = entry["meta"]["pages"]
    imgs = render_pages(ctx.raw_dir / f"{stem}.pdf", q_pages[:max_q], dpi)
    imgs += render_pages(ctx.raw_dir / f"{stem}_ans.pdf", a_pages[:max_a], dpi)
    return imgs


# ---------------------------------------------------------------- lint (type/garbage guard)

ALLOWED_PATCH = {"stem", "parts", "options", "answer", "solution"}
QNO_PREFIX = re.compile(r"^\s*\d{1,3}[.)]\s")
UNIT_NOTE = re.compile(r"\(Give your answer in ([^()\n]{1,20})\.\)")
UNIT_OK = re.compile(r"^\$?[A-Za-z][A-Za-z0-9^{}\\ ]{0,10}$|^[$¢°%]$|^[A-Za-z]{1,8}[23²³]?$")
VERDICT_TOKENS = {"ok", "fixed", "na", "severe", "unresolved", "n/a", "none"}


def norm_symbols(s: str) -> str:
    """Single deterministic text normalizer, shared with segment so clean-patched text
    gets the SAME treatment (¢, <img>→marker, [ANSWER], [QN], empty brackets, marks).
    The LLM can never bypass these by rewriting a field."""
    import interim_build as ib
    return ib.normalize_text(s)


def _texts_of(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [t for x in v.values() for t in _texts_of(x)]
    if isinstance(v, list):
        return [t for x in v for t in _texts_of(x)]
    return []


def lint_field(k: str, v) -> tuple[str | None, list[str]]:
    """Type/garbage guard only — NOT a quality judge.

    The LLM patching a field has more context (source text, whole question) than
    this function does; second-guessing it with dumb per-field rules reverted good
    fixes onto broken originals. So the ONLY hard reject is structural garbage a
    verdict-token value ("fixed"/"na"/...), which means the model confused the
    output format and the "content" is meaningless. Everything else is accepted;
    quality problems surface as review flags from the post-patch validator
    (refresh_flags) — flags, not gates.

    Returns (hard_error, soft_flags). Soft flags are advisory only.
    """
    texts = _texts_of(v)
    for t in texts:
        if t.strip().lower() in VERDICT_TOKENS:
            return f"verdict-token value {t.strip()!r}", []
    softs = []
    if k == "stem" and isinstance(v, str) and QNO_PREFIX.match(v):
        softs.append("stem_qno")
    for t in texts:
        for m in UNIT_NOTE.finditer(t):
            unit = m.group(1).strip().replace("\\$", "$")
            if not UNIT_OK.match(unit):
                softs.append("unit_note_review")
        s = t.replace("\\$", "")
        for tbl in re.findall(r"<table>.*?</table>", s, re.S):
            s = s.replace(tbl, "")
        if s.count("$") % 2 or s.count("{") != s.count("}"):
            softs.append("latex_suspect")
    return None, softs


def _coerce(k: str, v):
    if k in ("stem", "solution"):
        if not (isinstance(v, str) and v.strip()):
            return None
        return norm_symbols(v)
    if k == "answer":
        if isinstance(v, dict) and "value" in v:
            return {"value": norm_symbols(str(v["value"])), "kind": v.get("kind", "llm")}
        if isinstance(v, (str, int, float)) and str(v).strip():
            return {"value": norm_symbols(str(v).strip()), "kind": "llm"}
        return None
    if k == "options":
        if isinstance(v, dict) and v:
            return {str(a): norm_symbols(str(b)) for a, b in v.items()}
        return None
    if k == "parts":
        if not isinstance(v, list):
            return None
        import interim_build as ib
        flat = [{"no": str(p.get("no", "")), "text": str(p["text"])}
                for p in v if isinstance(p, dict) and p.get("text")]
        return ib.finalize_parts(flat)          # normalize + inline-split + nest
    return None


IMG_MARK = re.compile(r"!\[\]\([^)]+\)")


def _markers(x) -> list[str]:
    if isinstance(x, str):
        return IMG_MARK.findall(x)
    if isinstance(x, dict):
        return [m for v in x.values() for m in _markers(v)]
    if isinstance(x, list):
        return [m for p in x for m in _markers(p)]
    return []


def _restore_carry(old, new):
    """Re-attach per-part marks/answers to a patched parts tree, matched by canonical path.

    The prompt view (entry_view -> flatten_parts) shows the model only `no` and `text`, so a
    parts patch can only ever carry those two — everything else on the part (marks, answer,
    solution, answer_area) would be dropped on the floor when the patch replaces the tree.
    A value the rebuild derived from the new text (marks read out of a corrected "[2]") wins,
    hence setdefault."""
    import interim_build as ib
    if not isinstance(new, list):
        return new
    keep: dict[str, dict] = {}

    def index(ps, pre=""):
        for p in ps or []:
            key = pre + (p.get("no") or "")
            carry = {k: p[k] for k in ib.PART_CARRY if p.get(k) not in (None, "")}
            if carry:
                keep[key] = carry
            index(p.get("children") or [], key)

    index(old if isinstance(old, list) else [])
    if not keep:
        return new

    def restore(ps, pre=""):
        for p in ps:
            key = pre + (p.get("no") or "")
            for k, v in keep.get(key, {}).items():
                p.setdefault(k, v)
            restore(p.get("children") or [], key)

    restore(new)
    return new


def _restore_markers(old, new):
    """The LLM can't see figures and drops ![](...) markers it thinks are spurious.
    Re-append any marker present in old but missing from new (append to end of a
    string, or to the last part). Figures are real assets — never lose them."""
    missing = [m for m in _markers(old) if m not in _markers(new)]
    if not missing:
        return new
    tail = "\n" + "\n".join(missing)
    if isinstance(new, str):
        return new + tail
    if isinstance(new, list) and new:
        new[-1]["text"] = str(new[-1].get("text", "")) + tail
    return new


def apply_patch(entry: dict, patch: dict) -> tuple[list[str], list[str], list[str]]:
    """Apply patches. Only structural garbage (verdict-token values) is rejected;
    everything else is accepted and quality issues become review flags.
    Returns (changed_fields, hard_rejected, soft_flags)."""
    changed, rejected, softs = [], [], []
    for k, v in (patch or {}).items():
        if k not in ALLOWED_PATCH:
            continue
        if v is None:
            if k in ("answer", "solution") and entry.get(k) is not None:
                entry[k] = None
                changed.append(k)
            continue
        v = _coerce(k, v)
        if v is None:
            continue
        if k in ("stem", "solution", "parts"):
            v = _restore_markers(entry.get(k), v)   # never drop a figure marker
        if k == "parts":
            v = _restore_carry(entry.get(k), v)     # never drop marks / per-part answers
        hard, soft = lint_field(k, v)
        if hard:
            rejected.append(f"{k}: {hard}")
            continue
        if v != entry.get(k):
            entry[k] = v
            changed.append(k)
            softs.extend(soft)
    return changed, rejected, softs


# ---------------------------------------------------------------- main pass

def entry_view(r: dict) -> dict:
    import interim_build as ib
    v = {k: r[k] for k in ("qid", "kind", "stem", "options", "answer", "solution")
         if r.get(k) is not None}
    if r.get("parts"):                          # show flat with composite labels (LLM-friendly)
        v["parts"] = ib.flatten_parts(r["parts"])
    return v


# derived flags that must reflect post-patch state (recomputed each pass)
DERIVED = {"no_answer", "no_solution", "latex_suspect", "short_stem", "image_missing"}


def refresh_flags(r: dict, img_dir: Path) -> None:
    """After patches: normalize empty->null, then recompute derived flags so the
    dashboard shows the true state (e.g. a cleared solution -> no_solution)."""
    import interim_build as ib
    if isinstance(r.get("solution"), str) and not r["solution"].strip():
        r["solution"] = None
    if isinstance(r.get("stem"), str):
        r["stem"] = r["stem"].strip()
    # the model may relabel options (A/B/C/D) while patching — force the internal
    # convention back on, remapping the answer with them (same guard as segment).
    ib.canon_entry(r)
    r["flags"] = [f for f in r["flags"] if f.split(":")[0] not in DERIVED]
    ib.validate(r, img_dir)                     # re-adds derived flags in place
    seen, out = set(), []
    for f in r["flags"]:                          # dedupe, keep order
        if f not in seen:
            seen.add(f)
            out.append(f)
    r["flags"] = out


def clean_one(ctx: context.Ctx, stem: str, ep, log=print, cancel=None,
              flagged_only=False, limit=0, chem=False) -> dict:
    """M3 clean: single VLM pass over every entry. Ground truth = the question's own
    source; figure-bearing entries also get the rendered source page image(s) so the
    model reads graphs / picture-options / answer keys the text can't carry. Pure-text
    entries go through the same model without images. The entry already holds its
    (high-recall, possibly merged) solution from segmentation, which the model prunes."""
    vision = ep.get("vision", False)
    dpi = ep.get("dpi", 140)
    sys_prompt = SYS_T1 + (CHEM_RULE if chem else "")
    import interim_build as _ib
    rows = _ib.read_jsonl(ctx.interim_dir / f"{stem}.jsonl")   # verified whole-file read
    pages = page_texts(ctx, stem)

    audit = []
    stats = {"checked": 0, "with_images": 0, "fixed": 0, "severe": 0,
             "lint_dropped": 0, "llm_errors": 0}
    todo = [r for r in rows if r["flags"]] if flagged_only else rows
    if limit:
        todo = todo[:limit]
    todo_ids = {r["qid"] for r in todo}

    for i, r in enumerate(rows):
        if cancel is not None and cancel.is_set():
            break
        if r["qid"] not in todo_ids:
            continue
        stats["checked"] += 1
        imgs = entry_images(ctx, stem, r, dpi) if (vision and needs_images(r)) else []
        if imgs:
            stats["with_images"] += 1
        user = (f"SOURCE TEXT (this question's pages):\n{slice_for(r, pages)}\n\n"
                + ("PAGE IMAGE(S) of the original scan are attached — read figures/options/answers "
                   "from them.\n\n" if imgs else "")
                + f"EXTRACTED ENTRY (its solution may include another question's working — prune it):\n"
                f"{json.dumps(entry_view(r), ensure_ascii=False)}")
        v = chat(ep, sys_prompt, user, images=imgs or None)
        if not isinstance(v, dict) or not isinstance(v.get("fields"), dict):
            stats["llm_errors"] += 1
            audit.append({"qid": r["qid"], "verdict": "llm_error"})
            continue
        sev = v.get("severity", "ok")
        changed, rejected, softs = apply_patch(r, v.get("patch"))
        if rejected:
            stats["lint_dropped"] += len(rejected)
            r["flags"].append("patch_rejected:" + ",".join(s.split(":")[0] for s in rejected))
        if changed:
            stats["fixed"] += 1
            r["flags"].append("llm_fixed:" + ",".join(changed))
            for s in set(softs):
                if s not in r["flags"]:
                    r["flags"].append(s)
        if sev == "severe":
            stats["severe"] += 1
            r["flags"].append("llm_severe")           # -> human review
        refresh_flags(r, ctx.extracted_dir / stem)
        audit.append({"qid": r["qid"], "severity": sev, "img": bool(imgs), "fields": v["fields"],
                      "changed": changed, "lint_rejected": rejected, "reason": v.get("reason", "")})
        if (i + 1) % 10 == 0:
            log(f"  {stem}: {i+1}/{len(rows)} (fixed {stats['fixed']}, img {stats['with_images']}, "
                f"severe {stats['severe']})")

    with open(ctx.interim_dir / f"{stem}.clean.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {"file": stem, **stats}
    (ctx.interim_dir / f"{stem}.clean.log.json").write_text(
        json.dumps({"summary": summary, "audit": audit}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    log(f"  {stem}: checked {stats['checked']} ({stats['with_images']} w/image), "
        f"fixed {stats['fixed']}, severe {stats['severe']}, "
        f"lint dropped {stats['lint_dropped']}, errors {stats['llm_errors']}")
    return summary


def has_clean(ctx: context.Ctx, name: str) -> bool:
    return (ctx.interim_dir / f"{Path(name).stem}.clean.jsonl").is_file()


def clean_files(ctx: context.Ctx, names: list[str], log=print, cancel=None,
                flagged_only=False, limit=0, use_vlm=False, thinking=None, chem=False) -> dict:
    # Default: text LLM (fast). Figure safety comes from deterministic guards
    # (_restore_markers, <img>/option normalization) that run regardless of endpoint.
    # use_vlm=True opts into the vision model reading page images (accurate but slow:
    # ~1-2 min per figure question). thinking overrides config per run.
    # chem=True: also convert plain-latex chemistry to mhchem \ce{} (CHEM_RULE).
    ep = (vlm_endpoint(thinking) if use_vlm else None) or endpoint(thinking)
    if ep is None and not os.environ.get("QUESTGEN_LLM_MOCK"):
        log("llm_clean: no endpoint configured (config.yaml llm:) — skipped")
        return {"ok": [], "failed": {}, "skipped": True}
    if ep:
        log(f"llm_clean: {ep['model']} ({'vision, reads page images' if ep.get('vision') else 'text-only'})"
            + (" · chem \\ce{}" if chem else ""))
    ok, failed = [], {}
    for n in names:
        if cancel is not None and cancel.is_set():
            break
        stem = Path(n).stem
        if stem.endswith("_ans"):
            continue
        if not (ctx.interim_dir / f"{stem}.jsonl").is_file():
            log(f"skip (no interim): {stem}")
            continue
        log(f"clean: {stem}")
        try:
            clean_one(ctx, stem, ep, log, cancel, flagged_only, limit, chem=chem)
            ok.append(n)
        except Exception as e:
            failed[n] = str(e)
            log(f"  ERROR {stem}: {e}")
    log(f"clean finished: {len(ok)} ok, {len(failed)} failed {list(failed) or ''}")
    return {"ok": ok, "failed": failed}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--flagged-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--vlm", action="store_true", help="use vision model + page images (slow)")
    context.add_ctx_args(ap)
    a = ap.parse_args()
    ctx = context.ctx_from_args(a)
    names = (sorted(p.stem for p in ctx.interim_dir.glob("*.jsonl")
                    if not p.name.endswith(".clean.jsonl")) if a.all else a.files)
    if not names:
        ap.error("give stems or --all")
    clean_files(ctx, names, flagged_only=a.flagged_only, limit=a.limit, use_vlm=a.vlm)
