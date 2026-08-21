"""M3: LLM-driven segmentation, deterministic verbatim assembly.

The LLM only LABELS each MinerU block with a role; it never rewrites text. A
deterministic assembler then groups blocks between question-start markers and builds
entries VERBATIM from the original block content. That split is the point: the model
reads meaning ("this starts question 1") rather than matching a numbering convention,
so a new paper layout does not need new parsing rules.

Layout tables are rewritten into ordinary blocks first (table_split), because a table
reaches the labeler only as a short preview and one table can hold several questions.

Answer files (<stem>_ans) are labeled the same way; solutions pair to questions by qno.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import context
import interim_build as ib
import llm_clean
import table_split
from prompts import SYS_ANSWERS, SYS_LABEL

CHUNK = 90          # blocks per labeling call
OVERLAP = 6         # carried context blocks between chunks
PREVIEW = 160       # chars of block text shown to the labeler


def compact(blocks: list[dict]) -> str:
    lines = []
    for b in blocks:
        t = b.get("_text") or b.get("text") or ""
        if b["type"] in ("image",):
            t = "<figure>"
        elif b["type"] in ("table", "chart"):
            t = "<table/chart> " + (b.get("_text") or "")[:60]
        t = re.sub(r"\s+", " ", t).strip()[:PREVIEW]
        lines.append(f"[{b['_i']}] ({b['type']}) {t}")
    return "\n".join(lines)


# ---------------------------------------------------------------- labeling

def _heuristic_labels(blocks: list[dict]) -> dict:
    """MOCK fallback: bare-number or N. at block start = q; images/tables = figure."""
    out = {}
    for b in blocks:
        t = (b.get("_text") or b.get("text") or "").strip()
        if b["type"] in ("image",):
            out[b["_i"]] = {"role": "figure"}
        elif b["type"] in ("table", "chart"):
            out[b["_i"]] = {"role": "figure"}
        elif b["type"] in ("footer", "page_number", "page_footnote", "header"):
            out[b["_i"]] = {"role": "noise"}
        elif re.match(r"^\d{1,3}[.\s)]", t):
            out[b["_i"]] = {"role": "q", "label": re.match(r"^(\d{1,3})", t).group(1)}
        elif re.match(r"^\([a-z]\)", t):
            out[b["_i"]] = {"role": "part", "label": t[:3]}
        else:
            out[b["_i"]] = {"role": "body"}
    return out


def label_blocks(blocks: list[dict], ep, log=print, cancel=None) -> dict:
    """Return {block_index: {"role":..., "label":...}} for every block.

    Blocks carrying a `_role` hint were already decided structurally (see table_split) and
    are neither sent to the model nor overwritten by it."""
    hinted = {b["_i"]: {"role": b["_role"], "label": b.get("_label", "")}
              for b in blocks if b.get("_role")}
    blocks = [b for b in blocks if not b.get("_role")]
    if os.environ.get("QUESTGEN_LLM_MOCK") or ep is None:
        return {**_heuristic_labels(blocks), **hinted}
    sys = SYS_LABEL
    labels: dict = dict(hinted)
    i = 0
    while i < len(blocks):
        if cancel is not None and cancel.is_set():
            break
        chunk = blocks[max(0, i - OVERLAP):i + CHUNK] if i else blocks[:CHUNK]
        v = llm_clean.chat(ep, sys, "BLOCKS:\n" + compact(chunk))
        rows = v if isinstance(v, list) else (v or {}).get("labels") if isinstance(v, dict) else None
        if not isinstance(rows, list):
            log(f"  label chunk @{i}: bad response, heuristic fallback for chunk")
            for b in chunk:
                labels.setdefault(b["_i"], _heuristic_labels([b])[b["_i"]])
        else:
            for r in rows:
                if isinstance(r, dict) and "i" in r:
                    labels.setdefault(int(r["i"]), {"role": r.get("role", "body"),
                                                    "label": r.get("label", "")})
        i += CHUNK
    for b in blocks:  # fill any gaps
        labels.setdefault(b["_i"], {"role": "body"})
    return labels


# ---------------------------------------------------------------- table parsers
# Options and answer keys are often printed as tables; MinerU emits them as one
# <table> block. The LLM labels the block (option / solution); these deterministic
# parsers crack the regular table structure.

def _cells(html: str) -> list[str]:
    return [re.sub(r"<[^>]+>", " ", c).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", html, re.S)]


def parse_options(text: str) -> dict | None:
    """A/B/C/D (or 1-4) options from an option block, whether a <table> or inline
    text like 'A 1.2 B 1.8 C 4.8 D 20'. Returns {key: value} or None."""
    seq = _cells(text) if "<table" in text else re.split(r"\s+", text.strip())
    opts, i = {}, 0
    if "<table" not in text:
        # inline: walk tokens, a lone key letter starts a value until next key
        toks = seq
        cur = None
        for tok in toks:
            if re.fullmatch(r"[A-D1-4]", tok) and tok not in opts:
                cur = tok
                opts[cur] = ""
            elif cur:
                opts[cur] = (opts[cur] + " " + tok).strip()
        return opts if len(opts) >= 2 and all(opts.values()) else None
    while i < len(seq):
        c = seq[i]
        m = re.match(r"^([A-D1-4])[\s.)]*(.*)$", c)
        if m and m.group(1) not in opts:
            val = m.group(2).strip()
            if not val and i + 1 < len(seq):
                val = seq[i + 1]
                i += 1
            opts[m.group(1)] = val
        i += 1
    if len(opts) < 2:
        return None
    # reject degenerate label-only parse (value == its own key, e.g. {"A":"A"}):
    # the real option values (often display formulas) live in separate blocks;
    # keep the raw block instead so llm_clean can recover them.
    if sum(1 for k, val in opts.items() if val.strip() == k) >= len(opts) - 1:
        return None
    return opts


# ---------------------------------------------------------------- assembly (verbatim)

# roman numerals need their own alternative: a bare [a-z] only ever consumed ONE letter, so
# "(ii)"/"(iii)"/"(iv)" never matched and the marker stayed duplicated in the part text.
_PART_LABEL = re.compile(r"^\s*\(?\s*(?:[ivx]{2,4}|[a-z])\s*[).]\s*", re.I)


def _strip_part_label(txt: str, lab: str) -> str:
    """Drop the printed sub-part marker from the start of a part's text. Prefer the exact label
    the labeler reported ("(ii)"); fall back to a generic (a)/(ii)/(iv)/a. style marker."""
    if lab:
        m = re.match(r"^\s*" + re.escape(str(lab)) + r"\s*[).]?\s*", txt)
        if m:
            return txt[m.end():]
    m = _PART_LABEL.match(txt)
    return txt[m.end():] if m else txt


def assemble_questions(blocks: list[dict], labels: dict) -> list[dict]:
    entries, cur, cur_part, cur_section = [], None, None, ""

    def close():
        nonlocal cur, cur_part
        if cur:
            entries.append(cur)
        cur, cur_part = None, None

    def prov(b):
        """Provenance index: the ORIGINAL MinerU block, even for blocks a pass synthesised."""
        return b.get("_src_i", b["_i"])

    def open_q(b, qno="", stem="", flags=()):
        nonlocal cur, cur_part
        close()
        cur = {"qno": str(qno or len(entries) + 1), "section": cur_section, "stem": stem,
               "parts": [], "options": {}, "assets": [], "solution": [],
               "pages": {b["page_idx"]}, "blocks": [prov(b), prov(b)], "flags": list(flags)}
        cur_part = None
        return cur

    def place(b, role, lab, txt):
        """Add one content block to the open question (stem/part/option/asset/solution)."""
        nonlocal cur_part
        cur["pages"].add(b["page_idx"])
        cur["blocks"][1] = max(cur["blocks"][1], prov(b))
        is_media = b["type"] in ("image", "table", "chart")
        # options first: a table/inline that holds A-D is consumed structurally,
        # its rendered image is redundant (no asset).
        if role == "option":
            src = b.get("table_body") or b.get("_text") or txt
            parsed = parse_options(src)
            if parsed:
                cur["options"].update(parsed)
            else:
                cur["options"][lab or _next_opt(cur)] = src
            return
        if is_media:
            # every image/table/chart block carries a rendered img_path -> asset
            cur["assets"].append({"kind": b["type"], "img_path": b.get("img_path", ""),
                                  "page_idx": b["page_idx"], "bbox": b.get("bbox")})
            html = b.get("table_body") or ""
            line = html if html.strip() else f"![]({b.get('img_path','')})"
            if cur_part:
                cur_part["text_md"] += "\n" + line
            else:
                cur["stem"] += "\n" + line
            return
        if role == "solution":
            cur["solution"].append(txt)
            return
        if role == "part":
            body = _strip_part_label(txt, lab)
            cur_part = {"label": lab or f"({chr(97+len(cur['parts']))})", "text_md": body.strip()}
            cur["parts"].append(cur_part)
            return
        # body
        if cur_part:
            cur_part["text_md"] = (cur_part["text_md"] + "\n" + txt).strip()
        else:
            cur["stem"] = (cur["stem"] + "\n" + txt).strip()

    # --- content before the first "q" ------------------------------------------------------
    # Two shapes, told apart by whether a sub-part starts there:
    #   * a "part" appears  -> the paper opens straight at "(a)" with no stem, so no block could
    #     carry a number: open an IMPLICIT question (flagged) rather than lose the whole thing.
    #   * only body/figure/option -> that is the following question's context (the labeler put
    #     "q" on a later sentence): buffer it and merge it into that question, in reading order.
    roles = {i: labels.get(i, {}).get("role", "body") for i in (b["_i"] for b in blocks)}
    first_q = next((b["_i"] for b in blocks if roles[b["_i"]] == "q"), None)
    lead = [b for b in blocks if b["_i"] < (first_q if first_q is not None else 10 ** 9)
            and roles[b["_i"]] not in ("noise", "section")]
    lead_is_question = bool(lead) and (any(roles[b["_i"]] == "part" for b in lead) or first_q is None)
    pending = [] if lead_is_question else list(lead)

    for b in blocks:
        role = roles[b["_i"]]
        lab = labels.get(b["_i"], {}).get("label", "")
        txt = b.get("_text") or ""
        if role == "noise":
            continue
        if role == "section":                          # section heading -> numbering restarts here
            cur_section = re.sub(r"\s+", " ", (lab or txt)).strip()
            continue
        if role == "q":
            # strip a leading question number the labeler identified
            body = txt
            m = re.match(r"^\s*" + re.escape(str(lab)) + r"[.)\s]\s*", body) if lab else None
            if m:
                body = body[m.end():]
            elif lab:
                body = re.sub(r"^\s*\d{1,3}[.)\s]\s*", "", body)
            if pending:                                # orphan context -> this question, in order
                open_q(pending[0], lab, "", ("leading_content_merged",))
                for pb in pending:
                    place(pb, roles[pb["_i"]], labels.get(pb["_i"], {}).get("label", ""),
                          pb.get("_text") or "")
                place(b, "body", "", body.strip())
                pending = []
            else:
                open_q(b, lab, body.strip())
            continue
        if cur is None:
            if pending:                                # handled when the first "q" arrives
                continue
            open_q(b, "", "", ("implicit_question_start",))
        place(b, role, lab, txt)
    close()
    return entries


def _next_opt(cur: dict) -> str:
    n = len(cur["options"]) + 1
    return "ABCD"[n - 1] if n <= 4 else str(n)


def _compact_full(blocks: list[dict]) -> str:
    """Block listing for answer interpretation — full text/HTML (answers must be
    read in full, not previewed)."""
    lines = []
    for b in blocks:
        if b["type"] in ("page_number", "footer", "page_footnote"):
            continue
        if b["type"] == "image":
            t = "<figure>"
        elif b["type"] in ("table", "chart"):
            t = b.get("table_body") or b.get("_text") or "<figure>"
        else:
            t = b.get("_text") or ""
        lines.append(f"[{b['_i']}] ({b['type']}) {t}")
    return "\n".join(lines)


def _norm_section(s: str) -> str:
    """Section heading -> comparison key ('Section A' == 'SECTION  A.' == 'section-a')."""
    return re.sub(r"\W+", "", (s or "")).lower()


def interpret_answers(blocks: list[dict], ep, log=print, cancel=None) -> list[dict]:
    """LLM reads the answer file (ANY layout: table / double-column / mixed / Qn) and
    returns an ORDERED LIST of {section, qno, answer, solution, figs:[block_i]} in
    reading order. Records are keyed on (section, qno, answer, solution-prefix) so the
    chunk OVERLAP re-reads are merged, while GENUINE per-section restarts are kept as
    separate records (two 'Q7' in different sections, or with different content). Pairing
    to questions is match_answers' job; figs -> image assets is build_one's."""
    if os.environ.get("QUESTGEN_LLM_MOCK") or ep is None:
        return []
    by_i = {b["_i"]: b for b in blocks}
    out: list = []
    seen: dict = {}                                  # dedup key -> index into out
    cur_section = ""                                 # last heading seen — carried across chunks
    i = 0
    while i < len(blocks):
        if cancel is not None and cancel.is_set():
            break
        chunk = blocks[max(0, i - OVERLAP):i + CHUNK] if i else blocks[:CHUNK]
        # a section heading is printed once at the top of a (possibly long) section; later
        # chunks don't contain it, so tell the LLM which section is already in effect.
        ctx_note = (f'CONTEXT: the section heading in effect before the blocks below (not '
                    f'repeated in this excerpt) is "{cur_section}". Use it until a new '
                    f'heading appears.\n\n') if cur_section else ""
        v = llm_clean.chat(ep, "", SYS_ANSWERS + "\n" + ctx_note + "BLOCKS:\n" + _compact_full(chunk))
        rows = v if isinstance(v, list) else None
        if not rows:
            log(f"  answer chunk @{i}: bad response")
        else:
            for r in rows:
                if not isinstance(r, dict) or not r.get("qno"):
                    continue
                qno = re.sub(r"\D", "", str(r["qno"])) or str(r["qno"])
                section = re.sub(r"\s+", " ", str(r.get("section") or "")).strip()
                if section:
                    cur_section = section            # remember for the next chunk
                answer = str(r["answer"]) if r.get("answer") else None
                sol = str(r.get("solution") or "").strip()
                figs = [fi for fi in (r.get("figs") or []) if isinstance(fi, int) and fi in by_i]
                # dedup: within a real section (section,qno) is unique, so overlap re-reads
                # collapse even when the LLM reformats them between chunks (escaped $, ; vs
                # \n, extra steps). Without a section, keep content in the key so genuine
                # un-headed restarts (two different 'Q7') survive.
                # `part` is in the key: a key laid out per sub-part emits several records with
                # the same (section, qno), and they must NOT dedup into one.
                part = str(r.get("part") or "").strip()
                key = ((_norm_section(section), qno, part) if section
                       else ("", qno, part, (answer or "")[:40], sol[:60]))
                if key in seen:                       # overlap re-read -> merge into existing
                    rec = out[seen[key]]
                    if answer and not rec["answer"]:
                        rec["answer"] = answer
                    if len(sol) > len(rec["solution"]):   # keep the richer (longer) read
                        rec["solution"] = sol
                    rec["figs"].extend(fi for fi in figs if fi not in rec["figs"])
                    continue
                seen[key] = len(out)
                out.append({"section": section, "qno": qno, "part": str(r.get("part") or "").strip(),
                            "answer": answer, "solution": sol, "figs": figs})
        i += CHUNK
    return out


def merge_answer_records(answers: list[dict]) -> list[dict]:
    """Collapse the records of ONE question into one record.

    An answer key laid out per sub-part (a table row per "(a)", "(b)(i)", …) makes the
    interpreter emit several records that all carry the same question number. match_answers
    pairs a question with a single record, so the rest would be dropped silently — five of
    six answers, in the paper this was found on.

    Merging rebuilds the packed "(a) …; (b)(i) …" form the rest of the pipeline already
    understands, so split_by_parts can put each piece on its part. When the interpreter did
    not report a part label the pieces are still joined, keeping them visible for a human
    rather than lost."""
    out: list[dict] = []
    by_key: dict = {}
    for a in answers:
        key = (_norm_section(a.get("section", "")), str(a.get("qno", "")))
        first = by_key.get(key)
        if first is None:
            lab = str(a.get("part") or "").strip()
            if lab:                                   # keep it labelled like the ones merged in
                for field in ("answer", "solution"):
                    if (a.get(field) or "").strip():
                        a[field] = f"{lab} {a[field].strip()}"
            by_key[key] = a
            out.append(a)
            continue
        for field in ("answer", "solution"):
            piece = (a.get(field) or "").strip()
            if not piece:
                continue
            label = str(a.get("part") or "").strip()
            piece = f"{label} {piece}".strip() if label else piece
            prev = (first.get(field) or "").strip()
            first[field] = f"{prev}; {piece}" if prev else piece
        first.setdefault("figs", []).extend(a.get("figs") or [])
    return out


def match_answers(questions: list[dict], answers: list[dict]) -> list:
    """Pair each question (reading order) with one answer record, cascade-free.
    Pass 1: exact (section, qno) when the question carries a section heading.
    Pass 2: occurrence-order by qno for whatever is left — the i-th remaining 'Q7'
    question takes the i-th remaining 'Q7' answer. A miss stays local; it never shifts
    the pairing of later questions the way global renumbering would."""
    from collections import defaultdict, deque
    used = [False] * len(answers)
    result: list = [None] * len(questions)
    for qi, q in enumerate(questions):               # pass 1: section-exact
        qs = _norm_section(q.get("section", ""))
        if not qs:
            continue
        for ai, a in enumerate(answers):
            if used[ai] or _norm_section(a.get("section", "")) != qs:
                continue
            if a["qno"] == str(q.get("qno", "")):
                result[qi] = a
                used[ai] = True
                break
    queues: dict = defaultdict(deque)                # pass 2: occurrence-order by qno
    for ai, a in enumerate(answers):
        if not used[ai]:
            queues[a["qno"]].append(ai)
    for qi, q in enumerate(questions):
        if result[qi] is not None:
            continue
        dq = queues.get(str(q.get("qno", "")))
        if dq:
            result[qi] = answers[dq.popleft()]
    # Pass 3: a question whose number was never PRINTED (the paper opens straight at "(a)",
    # so assemble_questions numbered it by position) cannot match a key that uses the real
    # number. Its qno carries no information, so fall back to reading order over whatever
    # records are still unclaimed.
    taken = {id(a) for a in result if a is not None}
    leftover = deque(a for a in answers if id(a) not in taken)
    for qi, q in enumerate(questions):
        if result[qi] is None and "implicit_question_start" in (q.get("flags") or []) and leftover:
            result[qi] = leftover.popleft()
    return result


# ---------------------------------------------------------------- answer value

def norm_img_html(s: str) -> str:
    """MinerU/LLM sometimes emit <img src="x"/>; normalize to our ![](x) marker."""
    if not s:
        return s
    return re.sub(r'<img[^>]*\bsrc=["\']([^"\']+)["\'][^>]*/?>', r'![](\1)', s)


def extract_answer(solution: str, options: dict, has_parts: bool = False) -> dict | None:
    # multi-part questions can't be tail-extracted to a single value — leave to
    # the answer-key LLM pass / llm_clean; flag as no_answer instead of guessing.
    if has_parts:
        return None
    if not solution:
        return None
    m = re.match(r"^\(?([A-D1-4])\)?\b", solution.strip())
    if m and options:
        return {"value": m.group(1), "kind": "mcq_option"}
    tail = solution.strip().splitlines()[-1] if solution.strip() else ""
    m = re.search(r"=\s*([^=]{1,40})$", tail)
    if m:
        v = m.group(1).strip().strip("$").strip()
        if v and "$" not in v and re.search(r"\\[a-zA-Z]|\^|_\{", v):
            v = f"${v}$"
        if v:
            return {"value": v, "kind": "tail_extract"}
    return None


# ---------------------------------------------------------------- build

def build_one(ctx: context.Ctx, stem: str, ep, log=print, cancel=None) -> dict:
    blocks = ib.load_blocks(ctx, stem)
    blocks = table_split.split_tables(blocks)     # layout tables -> ordinary blocks (+role hints)
    labels = label_blocks(blocks, ep, log=log, cancel=cancel)
    unknown = sorted({b["_unknown_type"] for b in blocks if b.get("_unknown_type")})

    # answers: separate _ans file interpreted by the LLM (any layout);
    # figs are block indices -> resolve to image assets deterministically.
    answers: list = []
    ans_by_i: dict = {}
    if (ctx.extracted_dir / f"{stem}_ans").is_dir():
        ablocks = ib.load_blocks(ctx, f"{stem}_ans")
        ans_by_i = {b["_i"]: b for b in ablocks}
        answers = interpret_answers(ablocks, ep, log=log, cancel=cancel)

    raw = assemble_questions(blocks, labels)
    # layered pairing: (section, qno) exact, else same-qno occurrence order — a paper whose
    # numbering restarts per section has several "Q7", and each must pair with its own answer.
    answers = merge_answer_records(answers)      # a per-sub-part key emits one record per row
    matched = match_answers(raw, answers)
    out_rows = []
    for idx, e in enumerate(raw):
        qno = e["qno"]
        sol_inpaper = "\n".join(e["solution"]).strip()
        arec = matched[idx] or {}
        sol = arec.get("solution") or sol_inpaper or None
        opts = {k: v for k, v in e["options"].items()} or None
        if arec.get("answer"):
            ans = {"value": arec["answer"], "kind": "answer_key"}
        else:
            ans = extract_answer(sol or "", opts or {}, has_parts=bool(e["parts"]))
        # solution figures: LLM gave block indices; take verbatim bbox/provenance
        ans_imgs = []
        for fi in arec.get("figs", []):
            b = ans_by_i.get(fi)
            if b and b.get("img_path"):
                ans_imgs.append({"kind": b["type"], "path": b["img_path"], "src": "ans",
                                 "page": b["page_idx"], "bbox": b.get("bbox")})
        row = {
            "qid": f"{stem}-{len(out_rows)+1:03d}",
            "kind": "mcq" if opts else "question",
            "stem": norm_img_html(e["stem"].strip()),
            "parts": [{"no": p["label"], "text": norm_img_html(p["text_md"].strip())} for p in e["parts"]],
            "options": opts,
            "answer": ans,
            "solution": norm_img_html(sol) if sol else sol,
            "imgs": [{"kind": a["kind"], "path": a["img_path"], "src": "q",
                      "page": a["page_idx"], "bbox": a["bbox"]} for a in e["assets"]] + ans_imgs,
            "meta": {"source_id": ctx.source, "subject": ctx.subject, "stage": ctx.stage,
                     "level": ctx.level, "file": f"raw/{stem}.pdf",
                     "pages": sorted(e["pages"]), "qno": qno, "section": e.get("section", ""),
                     "blocks": e["blocks"],
                     **({"unknown_block_types": unknown} if unknown else {})},
            "flags": e["flags"],
        }
        ib.polish_row(row)
        ib.validate(row, ctx.extracted_dir / stem)
        out_rows.append(row)

    # Block accounting: content in, content used. A block the assembler never placed is data
    # LOST, not "nothing to do" — say so instead of silently reporting a short/empty file.
    content_blocks = [b["_i"] for b in blocks
                      if labels.get(b["_i"], {}).get("role", "body") not in ("noise", "section")]
    used = set()
    for e in raw:
        lo, hi = e["blocks"]
        used |= {i for i in content_blocks if lo <= i <= hi}
    dropped = [i for i in content_blocks if i not in used]
    report = {
        "file": stem, "questions": len(out_rows), "engine": "llm_segment",
        "with_solution": sum(1 for r in out_rows if ib.has_answer(r, "solution")),
        "with_answer": sum(1 for r in out_rows if ib.has_answer(r)),
        "mcq": sum(1 for r in out_rows if r["kind"] == "mcq"),
        "flagged": sum(1 for r in out_rows if r["flags"]),
        "blocks_content": len(content_blocks), "blocks_dropped": len(dropped),
        "flags": {},
    }
    if dropped:
        report["dropped_block_idx"] = dropped[:50]
    warns = []
    if content_blocks and not out_rows:
        warns.append(f"{len(content_blocks)} content blocks produced 0 questions — nothing entered the bank")
    elif dropped:
        warns.append(f"{len(dropped)}/{len(content_blocks)} content blocks were not placed into any question")
    if warns:
        report["warnings"] = warns
        for w in warns:
            log(f"  !! {stem}: {w}")
    for r in out_rows:
        for fl in r["flags"]:
            report["flags"][fl.split(":")[0]] = report["flags"].get(fl.split(":")[0], 0) + 1

    ctx.interim_dir.mkdir(parents=True, exist_ok=True)
    with open(ctx.interim_dir / f"{stem}.jsonl", "w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (ctx.interim_dir / f"{stem}.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    # observability: the LLM's structural decisions, so a bad run is inspectable
    audit = {"mineru_version": ib.mineru_version(ctx, stem),
             "unknown_block_types": unknown,
             "question_labels": {str(b["_i"]): labels.get(b["_i"], {}) for b in blocks},
             "answer_records": [{"section": a["section"], "qno": a["qno"],
                                 "answer": a["answer"], "figs": a["figs"],
                                 "solution_len": len(a["solution"])} for a in answers]}
    (ctx.interim_dir / f"{stem}.segment.log.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")
    # reference sidecar: every answer record the LLM parsed from the _ans file, kept
    # verbatim and in order (section, qno, full text, resolved fig images) so the
    # dashboard can surface answers the pairing above could NOT attach to a question.
    if answers:
        ref = []
        for a in answers:
            figs = [ans_by_i[fi]["img_path"] for fi in a.get("figs", [])
                    if ans_by_i.get(fi) and ans_by_i[fi].get("img_path")]
            ref.append({"section": a.get("section", ""), "qno": a.get("qno", ""),
                        "answer": a.get("answer"),
                        "solution": a.get("solution") or "", "figs": figs})
        (ctx.interim_dir / f"{stem}.answers.json").write_text(
            json.dumps(ref, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  {stem}: {report['questions']} questions "
        f"({report['mcq']} mcq, {report['with_solution']} w/solution, "
        f"{report['with_answer']} w/answer, {report['flagged']} flagged)")
    return report


def build_files(ctx: context.Ctx, names: list[str], log=print, cancel=None, thinking=None) -> dict:
    ep = llm_clean.endpoint(thinking)
    if ep is None and not os.environ.get("QUESTGEN_LLM_MOCK"):
        log("llm_segment: no LLM endpoint (config.yaml llm:) — cannot segment")
        return {"ok": [], "failed": {n: "no endpoint" for n in names}}
    log(f"llm_segment: {ep['model'] if ep else 'MOCK heuristic'}")
    ok, failed = [], {}
    for n in names:
        if cancel is not None and cancel.is_set():
            break
        stem = Path(n).stem
        if stem.endswith("_ans"):
            continue
        if not (ctx.extracted_dir / stem).is_dir():
            log(f"skip (not extracted): {stem}")
            continue
        log(f"segment: {stem}")
        try:
            build_one(ctx, stem, ep, log, cancel)
            ok.append(n)
        except Exception as e:
            failed[n] = str(e)
            log(f"  ERROR {stem}: {e}")
    log(f"segment finished: {len(ok)} ok, {len(failed)} failed {list(failed) or ''}")
    return {"ok": ok, "failed": failed}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true")
    context.add_ctx_args(ap)
    a = ap.parse_args()
    ctx = context.ctx_from_args(a)
    names = ([p.name for p in sorted(ctx.extracted_dir.iterdir())
              if p.is_dir() and not p.name.endswith("_ans")] if a.all else a.files)
    if not names:
        ap.error("give stems or --all")
    build_files(ctx, names)
