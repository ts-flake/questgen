#!/usr/bin/env python3
"""Regression tests for llm_clean._parse_json / _repair_json.

LaTeX in a JSON string collides with JSON's own escapes, and the two failure modes pull in
opposite directions: `\\ce` is not a JSON escape so the whole reply fails to parse (loud),
while `\\frac` and `\\times` ARE (`\\f`, `\\t`) so json.loads silently returns a formfeed or
a tab and the corruption reaches the bank. The fix must therefore repair the second case
WITHOUT breaking a genuine `\\n` line break, which is why only known macros are read as
LaTeX. Each case below is one of those three obligations.

    python3 tests/json_repair.py

No fixtures, no network, no content tree.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import llm_clean as lc  # noqa: E402

# (payload, expected value of key "a", what it guards)
VALUES = [
    # silently corrupted before: the macro's first letter is also a JSON escape letter
    (r'{"a":"$\frac{1}{2}$"}',             "$\\frac{1}{2}$",              r"\frac was formfeed+rac"),
    (r'{"a":"$3 \times 4$"}',              "$3 \\times 4$",               r"\times was tab+imes"),
    (r'{"a":"$\theta$"}',                  "$\\theta$",                   r"\theta was tab+heta"),
    (r'{"a":"$\rho$"}',                    "$\\rho$",                     r"\rho was CR+ho"),
    (r'{"a":"$\beta$"}',                   "$\\beta$",                    r"\beta was backspace+eta"),
    (r'{"a":"$x \neq y$"}',                "$x \\neq y$",                 r"\neq was newline+eq"),
    (r'{"a":"$\nabla f$"}',                "$\\nabla f$",                 r"\nabla was newline+abla"),
    (r'{"a":"$A \rightarrow B$"}',         "$A \\rightarrow B$",          r"\rightarrow"),
    (r'{"a":"$\text{cost}$"}',             "$\\text{cost}$",              r"\text"),
    (r'{"a":"\begin{cases}x\end{cases}"}', "\\begin{cases}x\\end{cases}", r"\begin / \end"),
    (r'{"a":"$\underline{x}$"}',           "$\\underline{x}$",            r"\underline vs \uXXXX"),
    # whole reply failed to parse before
    (r'{"a":"$\ce{C16H34}$"}',             "$\\ce{C16H34}$",              r"\ce"),
    (r'{"a":"$0.25\,\mathrm{dm^3}$"}',     "$0.25\\,\\mathrm{dm^3}$",     r"\, and \mathrm"),
    # must stay exactly as JSON means it
    ('{"a":"line1\\nline2"}',              "line1\nline2",                r"real \n line break"),
    ('{"a":"col1\\tcol2"}',                "col1\tcol2",                  r"real \t tab"),
    ('{"a":"say \\"hi\\""}',               'say "hi"',                    "escaped quote"),
    ('{"a":"$\\\\ce{H2O}$"}',              "$\\ce{H2O}$",                 r"correct \\ce untouched"),
    ('{"a":"$\\\\frac12$"}',               "$\\frac12$",                  r"correct \\frac untouched"),
    ('{"a":"caf\\u00e9"}',                 "café",                        r"\uXXXX"),
    ('{"a":"back\\\\slash"}',              "back\\slash",                 "escaped backslash"),
    ('{"a":"line1\nline2"}',               "line1\nline2",                "RAW control char in string"),
]

_OBJ = '{"fields":{"parts":"fixed"},"patch":{"parts":[{"no":"(a)","text":"x"}]}}'

# (payload, expected type name, what it guards)
SHAPES = [
    (_OBJ,                        "dict",     "strict object"),
    ("Here:\n" + _OBJ + "\ndone", "dict",     "object wrapped in prose"),
    ("```json\n" + _OBJ + "\n```", "dict",    "```json fence"),
    ('[{"i":1,"role":"q"}]',      "list",     "bare array (the labeler's shape)"),
    ('{"labels":[{"i":1}]}',      "dict",     "{labels: [...]}"),
    # never reach INSIDE a payload we could not parse: returning patch.parts as the whole
    # result is what made clean_one crash with "'list' object has no attribute 'get'"
    (_OBJ[:40],                   "NoneType", "truncated -> honest None, not the inner array"),
]


def main() -> int:
    fails = 0
    for raw, want, label in VALUES:
        v = lc._parse_json(raw)
        got = v.get("a") if isinstance(v, dict) else v
        ok = got == want
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label:34} {got!r}" + ("" if ok else f"  want {want!r}"))
    for raw, want, label in SHAPES:
        got = type(lc._parse_json(raw)).__name__
        ok = got == want
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label:34} -> {got}" + ("" if ok else f"  want {want}"))

    # the repair must be a no-op on anything already well-formed
    n = 0
    for line in (json.dumps(x, ensure_ascii=False) for x in
                 ({"s": "a\nb\tc"}, {"s": "$\\frac{1}{2}$"}, {"s": "café"},
                  {"s": "\\ce{H2O}"}, {"s": 'q"q'}, {"s": "back\\slash"})):
        n += 1
        if json.loads(lc._repair_json(line)) != json.loads(line):
            fails += 1
            print(f"  FAIL no-op on well-formed {line!r}")
    print(f"  ok   no-op on {n} well-formed payloads")

    print(f"\n{'FAILURES: ' + str(fails) if fails else 'all passed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
