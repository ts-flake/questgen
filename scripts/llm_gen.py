"""M5 AI question generation: use the accepted question bank as few-shot corpus to
generate NEW questions (+ optional solutions) for chosen topic(s) / difficulty.

Pure generation only — the dashboard owns the review queue (accept/reject) and storage.
Output entries use the same schema as the rest of the bank so an accepted question drops
straight in. Deterministic normalization (normalize_text / finalize_parts) is applied so
generated text is consistent with segmented/cleaned entries.
"""
from __future__ import annotations

import json
import random
import re
import time

import context
import bank
import interim_build as ib
import llm_clean

GEN_SYS = """You are an exam question author for a school question bank. You are given REFERENCE
questions and must write NEW ones on the same TARGET TOPICS.

NOVELTY — this is the most important rule. The reference questions exist ONLY to calibrate style,
format, difficulty and solution method. NEVER reuse their scenarios, contexts, characters, objects,
settings or numbers, and never merely reword them. Every question you write must be set in a clearly
DIFFERENT real-world situation from every reference AND from the other questions in this batch.
Taking a reference and swapping its numbers is a FAILURE. If a reference is about ribbons, do not
write about ribbons.

DIVERSITY — the batch must be varied. Each question uses a distinct context and targets a DIFFERENT
sub-skill / learning outcome of the topic; vary the quantities, the numeric ranges and the phrasing.
No two questions in the batch may share a scenario or a near-identical structure.

SOLUTIONS — write each solution in the SOLUTION STYLE given below, and MATCH the METHOD and the
LENGTH of the reference solutions (if the references solve by a certain method, you use that method,
not another). Show ONLY the final, clean steps a teacher would write. Do ALL reasoning/checking
SILENTLY first; never include trial-and-error, self-correction, or meta-commentary ("let me try",
"wait", "hmm", "let me check", "that's not nice"), and never restate or second-guess the question.
Pick clean numbers up front so the answer is tidy. Keep it short.

Output ONE JSON array; each element is a question object:
{"stem": "the question text",
 "parts": [{"no":"(a)","text":"...","children":[{"no":"(i)","text":"..."},{"no":"(ii)","text":"..."}]},{"no":"(b)","text":"..."}],  // [] if none; nest sub-parts (i),(ii) under their parent via "children"
 "options": {"(1)":"...","(2)":"...","(3)":"...","(4)":"..."} or null,   // only for multiple-choice
 "answer": "final answer(s); an MCQ answer is its option label, e.g. '(3)'; for sub-parts label them e.g. '(a) $5$; (b) $9.4$'",
 "solution": "worked solution (or \\"\\" if solutions were not requested)",
 "type": "mcq|fill_blank|short_answer|structured|word_problem|true_false",
 "topics": ["<one or more ids taken from the TARGET TOPICS list this question covers>"]}

Rules: wrap ALL mathematics in $...$. Use "parts" for labelled sub-parts (a),(b),(i)...; otherwise [].
Use "options" ONLY for MCQ. Each question must be self-contained and solvable. "topics" MUST be a
subset of the TARGET TOPICS ids. Output ONLY the JSON array — no prose, no markdown fences."""


def example_of(r: dict) -> dict:
    """Bank entry -> display-friendly few-shot example dict (with qid/topic/fs for the viewer)."""
    tg = r.get("tags") or {}
    fs = (r.get("meta") or {}).get("file", "")
    fs = fs.split("/")[-1].rsplit(".", 1)[0] if fs else ""
    return {"qid": r.get("qid", ""), "topic": tg.get("topic") or [],
            "difficulty": tg.get("difficulty", ""), "fs": fs,
            "stem": r.get("stem", ""),
            "parts": r.get("parts") or [],     # full nested tree (children + marks) — used for display AND the prompt
            "options": r.get("options"),
            "answer": (r.get("answer") or {}).get("value") if isinstance(r.get("answer"), dict) else r.get("answer"),
            "solution": r.get("solution") or ""}


def topic_bank(ctx: context.Ctx, topics: list[str]) -> list[dict]:
    """Every bank entry (this subject/stage) tagged with ANY of the target topics — the pool
    for both few-shot sampling and the novelty check."""
    tset = set(topics)
    return [r for r in bank.collect(ctx)
            if tset & set((r.get("tags") or {}).get("topic") or [])]


def bank_examples(ctx: context.Ctx, topics: list[str], difficulty: str, k: int,
                  qtype: str = "", pool: list[dict] | None = None) -> list[dict]:
    """Sample up to k bank entries matching ANY selected topic (+ difficulty, + type) as corpus."""
    rows = pool if pool is not None else topic_bank(ctx, topics)
    cand = [r for r in rows
            if (not difficulty or (r.get("tags") or {}).get("difficulty") == difficulty)
            and (not qtype or (r.get("tags") or {}).get("type") == qtype)]
    random.shuffle(cand)
    return [example_of(r) for r in cand[:k]]


# --------------------------------------------------------------- solution style (per stage)
# The level-appropriate way to WRITE solutions. Read from a per-subject/stage override file
# (<subject>/<stage>/_alignment/gen_style.md) if present, else a built-in default. Keeps the
# generator from defaulting to algebra where the curriculum uses arithmetic / bar models.
_STYLE_DEFAULTS = {
    ("math", "primary"): ("Singapore primary-school methods ONLY: reason with bar / model diagrams "
                          "in 'units', the unitary method, and arithmetic. Do NOT use algebra — no "
                          "'let x = …', no setting up equations. Keep it to a few short lines."),
    ("math", "secondary"): "Concise working; algebra is fine. A few lines.",
    ("physics", "jc"): ("State the relevant principle/formula, substitute values WITH units, and give "
                        "the numeric answer to appropriate significant figures. Concise working."),
}


def solution_style(ctx: context.Ctx) -> str:
    p = ctx.stage_dir / "_alignment" / "gen_style.md"
    try:
        if p.is_file():
            t = p.read_text(encoding="utf-8").strip()
            if t:
                return t
    except Exception:
        pass
    return (_STYLE_DEFAULTS.get((ctx.subject, ctx.stage))
            or "Match the method and length of the reference solutions; keep it concise.")


# --------------------------------------------------------------- novelty check (anti-duplication)
# A deterministic similarity guard so near-copies of the bank/reference don't sneak through as
# "new". Compares each generated stem against the references AND the whole topic bank; the score
# rides on meta.gen.novelty and the review card badges it (flag only — nothing is auto-dropped).
_WORD = re.compile(r"[a-z0-9]+")
NEAR_DUP = 0.5


def _ntok(s: str) -> list[str]:
    s = re.sub(r"\$[^$]*\$", " ", (s or "").lower())     # drop math spans — numbers vary, scenario is the tell
    return _WORD.findall(s)


def _jac(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _similarity(s1: str, s2: str) -> float:
    t1, t2 = _ntok(s1), _ntok(s2)
    if not t1 or not t2:
        return 0.0
    bg1 = set(zip(t1, t1[1:])) or set(t1)
    bg2 = set(zip(t2, t2[1:])) or set(t2)
    return max(_jac(set(t1), set(t2)), _jac(bg1, bg2))   # word + bigram overlap


def _entry_stem_text(e: dict) -> str:
    stem = e.get("stem") or ""
    if not stem.strip():
        stem = " ".join(p.get("text", "") for p in ib.flatten_parts(e.get("parts") or []))
    return stem


def novelty_scan(entries: list[dict], refs: list[dict], bank_rows: list[dict]) -> None:
    """Attach meta.gen.novelty = {score, match, where} to each entry (max similarity to the
    reference set + topic bank). score >= NEAR_DUP means "too close to an existing question"."""
    pool = [(e.get("qid", ""), "reference", e.get("stem", "")) for e in (refs or [])] \
        + [(r.get("qid", ""), (r.get("meta") or {}).get("source_id", "bank"), _entry_stem_text(r))
           for r in (bank_rows or [])]
    for ent in entries:
        stem = _entry_stem_text(ent)
        best = (0.0, "", "")
        for qid, where, other in pool:
            sc = _similarity(stem, other)
            if sc > best[0]:
                best = (sc, qid, where)
        ent.setdefault("meta", {}).setdefault("gen", {})["novelty"] = {
            "score": round(best[0], 2), "match": best[1], "where": best[2]}


def _to_entry(r: dict, i: int, ts: str, ctx: context.Ctx, ep: dict, topics: list[str],
              difficulty: str, with_solutions: bool, custom_prompt: str) -> dict | None:
    """Wrap one LLM question object into a bank entry (normalized + nested parts)."""
    if not isinstance(r, dict) or not (str(r.get("stem", "")).strip() or r.get("parts")):
        return None
    # accept either flat composite labels or nested children -> flatten to composite, then re-nest
    flat = [{"no": str(p.get("no", "")), "text": str(p.get("text", ""))}
            for p in ib.flatten_parts(r.get("parts") or []) if isinstance(p, dict)]
    parts = ib.finalize_parts(flat)
    opts = r.get("options") if isinstance(r.get("options"), dict) and r.get("options") else None
    if opts:
        opts = {str(k): ib.normalize_text(str(v)) for k, v in opts.items()}
    ans = r.get("answer")
    ansd = {"value": str(ans), "kind": "ai"} if ans else None
    if opts:                                  # internal convention: keys "(1)".."(n)"
        opts, ansd = ib.canon_options(opts, ansd)
    sol = str(r.get("solution", "")).strip() if with_solutions else ""
    ai_topics = [t for t in (r.get("topics") or []) if t in set(topics)]
    return {
        "qid": f"gen-{ts}-{i:03d}",
        "kind": "mcq" if opts else "question",
        "stem": ib.normalize_text(str(r.get("stem", ""))),
        "parts": parts,
        "options": opts,
        "answer": ansd,
        "solution": ib.normalize_text(sol) if sol else None,
        "imgs": [],
        "tags": {"topic": ai_topics or list(topics),
                 "type": r.get("type") or ("mcq" if opts else "structured" if parts else "short_answer"),
                 "difficulty": difficulty or "medium"},
        "meta": {"source_id": "ai_generated", "subject": ctx.subject, "stage": ctx.stage,
                 "level": ctx.level, "file": "ai_generated", "qno": "", "section": "",
                 "gen": {"model": ep.get("model"), "topics": list(topics), "difficulty": difficulty,
                         "prompt": custom_prompt.strip(), "ts": ts}},
        "flags": ["ai_generated"],
    }


def generate(ctx: context.Ctx, ep: dict, topics: list[str], difficulty: str, n: int,
             with_solutions: bool, custom_prompt: str = "", k: int = 5,
             topic_names: dict | None = None,
             examples: list[dict] | None = None,
             objectives: list[dict] | None = None,
             qtype: str = "") -> tuple[list[dict], list[dict]]:
    """Generate up to n new question entries. Returns (entries, reference_examples).
    If `examples` is given (user-picked bank questions) it is used as the few-shot corpus;
    otherwise k entries are sampled by topic/difficulty/type. reference_examples is what was fed."""
    topics = [t for t in topics if t]
    topic_names = topic_names or {}
    pool = topic_bank(ctx, topics)                    # whole topic bank (few-shot + novelty)
    ex = examples if examples else bank_examples(ctx, topics, difficulty, k, qtype, pool=pool)
    tlist = ", ".join(t + (f" ({topic_names[t]})" if topic_names.get(t) else "") for t in topics)
    parts = [
        f"THESE QUESTIONS ARE FOR: subject={ctx.subject}, stage={ctx.stage}, level={ctx.level}.",
        f"TARGET TOPICS: {tlist}",
        f"DIFFICULTY: {difficulty or 'any'}",
    ] + ([f"TARGET QUESTION TYPE: {qtype} (every generated question must be this type)"] if qtype else []) + [
        f"GENERATE: {n} new question(s)" + (
            " WITH worked solutions." if with_solutions else ", solutions NOT required (leave solution \"\")."),
    ]
    if objectives:
        parts.append("\nLEARNING OBJECTIVES TO ASSESS (spread the questions across these so each is "
                     "covered — cycle through them; do not target only one):\n"
                     + "\n".join(f"- [{o.get('topic', '')}] {o.get('lo', '')}" for o in objectives))
    else:
        parts.append("\nDIVERSITY: make the questions cover DIFFERENT sub-skills of the topic(s) — "
                     "do not cluster them on one skill or one kind of scenario.")
    if with_solutions:
        parts.append("\nSOLUTION STYLE (write every solution exactly this way):\n" + solution_style(ctx))
    if custom_prompt.strip():
        parts.append("\nAUTHOR INSTRUCTIONS:\n" + custom_prompt.strip())
    if ex:
        proj = [{"stem": e["stem"], "parts": e["parts"], "options": e["options"],
                 "answer": e["answer"], "solution": e["solution"]} for e in ex]
        parts.append(f"\nREFERENCE QUESTIONS ({len(ex)}) — STYLE / FORMAT / DIFFICULTY / SOLUTION-METHOD "
                     "CALIBRATION ONLY. Do NOT reuse their scenarios, contexts, objects or numbers; do "
                     "NOT reword them. Keep: layout, difficulty, how the solution is worked. Change: the "
                     "situation, the quantities, the numbers.\n"
                     + json.dumps(proj, ensure_ascii=False, indent=1))
    else:
        parts.append("\n(No reference questions in the bank for these topics — use your judgement.)")
    parts.append(f"\nOutput a JSON array of exactly {n} NEW question object(s), each in a DIFFERENT "
                 "scenario from the references and from each other.")
    v = llm_clean.chat(ep, GEN_SYS, "\n".join(parts))
    rows = v if isinstance(v, list) else (
        v.get("questions") if isinstance(v, dict) and isinstance(v.get("questions"), list) else None)
    if not rows:
        return [], ex
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = []
    for i, r in enumerate(rows, 1):
        e = _to_entry(r, i, ts, ctx, ep, topics, difficulty, with_solutions, custom_prompt)
        if e:
            out.append(e)
    novelty_scan(out, ex, pool)                       # flag near-duplicates for review
    return out, ex
