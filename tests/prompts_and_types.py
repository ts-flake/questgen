#!/usr/bin/env python3
"""Structure tests for the prompt module and the shape→type rule.

Two things that rot silently:

  * the prompts have to agree with each other and with docs/INTERIM_SCHEMA.md. They only
    disagreed in the first place because they lived in four different files — SYS_T1's rule 6
    told the model to leave a unit as bare text while rule 4, four lines above, required it
    upright in \\mathrm. These assertions pin the conventions that span prompts.
  * `problem_types` is edited by the user in Settings, so nothing may hardcode a type id.
    The old tag fallback assigned "short_answer" even to a bank whose vocabulary did not
    contain it.

    python3 tests/prompts_and_types.py

No fixtures, no network, no content tree.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import interim_build as ib  # noqa: E402
import prompts  # noqa: E402


def check(label: str, cond: bool) -> int:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    return 0 if cond else 1


def prompt_checks() -> int:
    f = 0
    every = {n: getattr(prompts, n) for n in
             ("SYS_LABEL", "SYS_ANSWERS", "SYS_T1", "CHEM_RULE", "SYS_TMPL", "GEN_SYS")}
    f += check("all six prompts present and non-empty", all(v.strip() for v in every.values()))

    # the placeholder vocabulary is closed (schema §3.5) — a prompt must not teach a new one
    known = {"[ANSWER]", "[QN]", "[BRACKETS]"}
    invented = {m for v in every.values() for m in re.findall(r"\[[A-Z][A-Z_]{2,}\]", v)} - known
    f += check(f"no prompt invents a placeholder token {sorted(invented) or ''}", not invented)

    # units: rule 4 sets the convention, rule 6's examples once contradicted it
    t1 = every["SYS_T1"]
    unit_examples = re.findall(r'Right: "([^"]*\[ANSWER\][^"]*)"', t1)
    f += check("SYS_T1 has [ANSWER] examples to check", bool(unit_examples))
    bare = [e for e in unit_examples
            if re.search(r"\[ANSWER\]\s+[A-Za-z]", e) and "\\mathrm" not in e]
    f += check(f"every [ANSWER] example keeps its unit in \\mathrm {bare or ''}", not bare)

    # option keys are the bank's internal convention, not the paper's letters
    f += check('SYS_T1 states the "(1)","(2)" option convention',
               '"(1)","(2)"' in t1 or '(1)","(2)' in t1)
    # the tag prompt must take its vocabulary from the caller, never name a type itself
    tmpl = every["SYS_TMPL"]
    f += check("SYS_TMPL takes types via {types}", "{types}" in tmpl)
    hardcoded = [t for t in ("short_answer", "word_problem", "structured")
                 if t in tmpl.replace("{types}", "")]
    f += check(f"SYS_TMPL hardcodes no type id {hardcoded or ''}", not hardcoded)

    # the prompts must live in prompts.py only
    strays = []
    for p in sorted((ROOT / "scripts").glob("*.py")):
        if p.name == "prompts.py":
            continue
        src = p.read_text(encoding="utf-8")
        for name in every:
            if re.search(rf"^{name}\s*=", src, re.M):
                strays.append(f"{p.name}:{name}")
    f += check(f"no prompt is redefined outside prompts.py {strays or ''}", not strays)
    return f


def type_checks() -> int:
    f = 0
    mcq = {"options": {"(1)": "a"}, "parts": []}
    parts = {"options": None, "parts": [{"no": "(a)"}]}
    plain = {"options": None, "parts": []}

    f += check("shape: options -> mcq", ib.entry_shape(mcq) == "mcq")
    f += check("shape: parts -> structured", ib.entry_shape(parts) == "structured")
    f += check("shape: neither -> None", ib.entry_shape(plain) is None)
    f += check("shape: options outrank parts (kind is mcq iff options)",
               ib.entry_shape({"options": {"(1)": "a"}, "parts": [{"no": "(a)"}]}) == "mcq")

    default = ("mcq", "short_answer", "structured", "word_problem")
    f += check("default vocabulary resolves", ib.structural_type(mcq, default) == "mcq"
               and ib.structural_type(parts, default) == "structured")

    renamed = ("multiple_choice", "open_response", "multipart_question")
    f += check("renamed vocabulary resolves",
               ib.structural_type(mcq, renamed) == "multiple_choice"
               and ib.structural_type(parts, renamed) == "multipart_question")

    zh = ("选择题", "填空题", "结构化大题")
    f += check("translated vocabulary resolves", ib.structural_type(mcq, zh) == "选择题")

    exact_first = ("mcq_hard", "mcq", "other")
    f += check("an exact id beats a partial word match",
               ib.structural_type(mcq, exact_first) == "mcq")

    none_fit = ("calculation", "proof", "application")
    f += check("a vocabulary with no word for the shape yields nothing",
               ib.structural_type(mcq, none_fit) is None
               and ib.structural_type(parts, none_fit) is None)
    f += check("an empty vocabulary yields nothing", ib.structural_type(mcq, ()) is None)

    for vocab in (default, renamed, zh, none_fit, ()):
        got = [ib.structural_type(e, vocab) for e in (mcq, parts, plain)]
        if any(g is not None and g not in vocab for g in got):
            f += check(f"never returns an id outside the vocabulary {vocab}", False)
            break
    else:
        f += check("never returns an id outside the vocabulary it was given", True)
    return f


def main() -> int:
    fails = prompt_checks() + type_checks()
    print(f"\n{'FAILURES: ' + str(fails) if fails else 'all passed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
