"""M3: extracted/<stem>/content_list.json -> interim/<stem>.jsonl  (THE product).

Deterministic pipeline: preprocess -> zone split -> segment questions -> parse answers
-> pair -> validate. Text is assembled VERBATIM from MinerU blocks (only whitespace
normalization inside math); nothing is rewritten. 公式保持 $ / $$, 表格保持 HTML。
Every defect becomes a flag on the entry — nothing is silently dropped.

The row contract (fields, label formats, placeholder vocabulary) lives in
docs/INTERIM_SCHEMA.md. It is not restated here: two copies drift, and the copy in the
code is the one nobody updates.

CLI:  python3 scripts/interim_build.py --all [--source book_math_worksheet_18]
"""
from __future__ import annotations

import glob
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import context


# ------------------------------------------------- robust reads (iCloud dataless files)
# The content tree can live in an iCloud-synced folder (~/Desktop is one when "Desktop &
# Documents" sync is on). iCloud EVICTS file data: the file still lists with its real
# st_size, but its bytes are remote — the flags read `compressed,dataless`.
#
# Measured behaviour on such a file (macOS 2026-07):
#   `for line in open(p)` / `f.read(8192)`  -> 0 bytes, no error, no download triggered
#   `p.read_bytes()` / `f.read(1<<20)`      -> blocks ~1-2 s, materialises, full content
# i.e. a small or line-buffered read silently reports EOF. Since every jsonl here is
# read-then-rewritten, that is a data-loss bug (rewriting a bank file from 0 entries),
# and it is why a cold bank looked half empty. So: read whole-file, verify the byte
# count, fail loudly rather than hand back a truncated list.
# `prefetch` warms many files at once, because the wait is latency, not bandwidth.

class ShortRead(IOError):
    """A file read back fewer bytes than it claims to have (iCloud download pending)."""


def read_bytes_whole(path: Path, tries: int = 5) -> bytes:
    path = Path(path)
    size, data = path.stat().st_size, b""
    for k in range(tries):
        size = path.stat().st_size
        data = path.read_bytes()                 # whole-file read → forces materialisation
        if len(data) >= size:
            return data
        time.sleep(0.2 * (k + 1))
    raise ShortRead(f"{path.name}: read {len(data)}/{size} bytes — 文件可能仍在从 iCloud 下载")


def read_jsonl(path: Path, required: bool = True) -> list[dict]:
    """Every jsonl read in the project goes through here. `required` (default) means a
    non-empty file MUST yield at least one parsable entry — otherwise we raise instead of
    letting a caller rewrite the file from an empty list."""
    path = Path(path)
    data = read_bytes_whole(path)
    rows, bad = [], 0
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            bad += 1
    if required and not rows and data.strip():
        raise ShortRead(f"{path.name}: {len(data)} bytes but no readable entries "
                        f"({bad} unparsable lines) — 拒绝按空文件处理")
    return rows


_MATERIALIZED: set = set()


def materialize(path) -> None:
    """Force one file's data to be local, once per process. Cheap insurance before code
    that reads a file in small chunks (serving a PDF by byte range, streaming a PDF into
    the MinerU upload): 1 MB chunks do trigger the download, but an 8 KB one returns EOF,
    so anything chunk-size-dependent should not have to care."""
    p, key = Path(path), str(path)
    if key in _MATERIALIZED:
        return
    try:
        p.read_bytes()          # one whole-file read; chunked/buffered reads do NOT
    except Exception:           # trigger the download and just report EOF
        return
    _MATERIALIZED.add(key)


def prefetch(paths, workers: int = 8) -> None:
    """Materialise files concurrently (best effort, never raises). Serial iCloud
    downloads dominate load time: 30 files × ~1.5 s → a couple of seconds."""
    paths = [Path(p) for p in paths]
    if len(paths) < 2:
        return
    def touch(p):
        try:
            p.read_bytes()
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(touch, paths))

SCHEMA_VERSION = 2          # interim row contract; see docs/INTERIM_SCHEMA.md
# per-part fields that are DATA, not derived from text: they must survive re-nesting
PART_CARRY = ("marks", "answer", "solution", "answer_area")

NOISE_TYPES = {"page_number", "footer", "page_footnote", "header"}
BOILERPLATE = re.compile(r"^(©|\d{1,4}$|Educational Publishing|Page \d)", re.I)
CODE_DIV = re.compile(r"^<div[^>]*>\n?|\n?</div>$|^```\w*\n?|\n?```$")


# ---------------------------------------------------------------- text utils

def fix_math_spacing(s: str) -> str:
    """Collapse MinerU vlm token spacing inside $...$ / $$...$$ only.
    '1 1 x + (4 x + 2 x)' -> '11x + (4x + 2x)'. digit-digit join is a correctness fix."""
    def fix(m):
        t = m.group(0)
        while True:
            t2 = re.sub(r"(?<=\d) (?=\d)", "", t)          # 1 1 -> 11
            t2 = re.sub(r"(?<=\d) (?=[a-zA-Z](?![a-zA-Z]))", "", t2)  # 4 x -> 4x
            if t2 == t:
                return t2
            t = t2
    return re.sub(r"\$\$?[^$]+\$\$?", fix, s)


def block_text(b: dict) -> str:
    """Verbatim text of a block; code divs unwrapped; tables stay HTML."""
    t = b["type"]
    if t == "code":
        s = b.get("code_body", "")
        return CODE_DIV.sub("", CODE_DIV.sub("", s)).strip()
    if t == "table":
        return b.get("table_body", "").strip()
    if t == "image":
        return f"![]({b.get('img_path','')})"
    return (b.get("text") or "").strip()


def latex_ok(s: str) -> bool:
    s = s.replace("\\$", "")  # escaped currency dollars are not math delimiters
    if s.count("$") % 2:
        return False
    for m in re.findall(r"\$\$?([^$]*)\$\$?", s):
        if m.count("{") != m.count("}"):
            return False
        if re.search(r"\\(frac|sqrt|angle|times|div|quad|circ|percent)[a-zA-Z]", m):
            return False
    return True


# ---------------------------------------------------------------- MinerU adapter
# Minimal anti-corruption surface: the ONLY place that resolves MinerU's file
# layout and its (unversioned, already-drifted) block schema. Downstream reads the
# returned blocks. Unknown block types are NOT dropped or fatal — they degrade to
# text (block_text fallback) and get flagged, per the never-crash invariant.

KNOWN_TYPES = {"text", "equation", "image", "table", "chart", "code",
               "header", "footer", "page_number", "page_footnote", "aside_text"}


def content_list_path(ctx: context.Ctx, stem: str) -> Path | None:
    hits = glob.glob(str(ctx.extracted_dir / stem / "*content_list.json"))
    hits = [h for h in hits if "_v2" not in h] or hits
    return Path(hits[0]) if hits else None


def mineru_version(ctx: context.Ctx, stem: str) -> str | None:
    try:
        return json.loads((ctx.extracted_dir / stem / "layout.json").read_text()).get("_version_name")
    except Exception:
        return None


# ---------------------------------------------------------------- core

def load_blocks(ctx: context.Ctx, stem: str) -> list[dict]:
    p = content_list_path(ctx, stem)
    if p is None:
        return []
    blocks = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for i, b in enumerate(blocks):
        rescued = False
        if b["type"] not in KNOWN_TYPES:
            b = dict(b, _unknown_type=b["type"])  # MinerU drift: keep, flag later
        if b["type"] in NOISE_TYPES:
            # MinerU sometimes mislabels real content as footer/page_footnote.
            # True page furniture sits in the top/bottom strip (bbox is 0-1000).
            y0, y1 = (b.get("bbox") or [0, 0, 0, 1000])[1], (b.get("bbox") or [0, 0, 0, 1000])[3]
            t = (b.get("text") or "").strip()
            in_strip = y0 >= 870 or y1 <= 130
            if in_strip or BOILERPLATE.match(t) or len(t) < 15:
                continue
            b, rescued = dict(b, type="text"), True  # rescue as text
        b = dict(b, _i=i, _text=fix_math_spacing(block_text(b)))
        if rescued:
            b["_rescued"] = True
        out.append(b)
    return out


# ---------------------------------------------------------------- polish

# Only SOURCE-AGNOSTIC, mechanical rules live here. Anything that needs flexible
# interpretation (answer placeholders 'Ans:/ans:/answers:/____' and their units,
# severed latex, layout damage) is llm_clean's job — see its SYS_T1.

ROMAN = re.compile(r"i{1,3}|iv|v|vi{0,3}|ix|x", re.I)


def part_depth(no: str) -> int:
    """Nesting depth from a part label: (a)=1, (b)(i)/(i)/(ii)=2, (b)(i)(A)=3."""
    groups = re.findall(r"\([^)]*\)", no or "")
    if len(groups) >= 2:
        return len(groups)
    inner = (groups[0] if groups else (no or "")).strip("() ").lower()
    return 2 if ROMAN.fullmatch(inner) else 1


def local_label(no: str) -> str:
    """Last parenthesised group is the LOCAL label: (b)(i) -> (i), (a) -> (a)."""
    no = canon_part_no(no)
    groups = re.findall(r"\([^)]*\)", no or "")
    return groups[-1] if groups else (no or "")


# ------------------------------------------------------------ canonical labels
# ONE internal label convention, so every stage/viewer/exporter agrees:
#   part labels  -> always parenthesised:  "(a)", "(i)", "(b)(ii)"
#   mcq options  -> keys "(1)".."(n)" in the paper's order
# Display formats (A./B., 1., bare letters) are an EXPORT choice only — see
# export_docx.option_label. Both helpers are idempotent and are applied by
# polish_row (segment), llm_clean.refresh_flags, llm_gen and the dashboard editor,
# so no writer can leave a non-canonical label behind.

_LABEL_TOK = re.compile(r"\(([^)]*)\)|([A-Za-z]+|\d+)")
# a plausible part label: a digit run, one/two letters, or a roman numeral — so a stray
# word ("Part a") is left alone instead of being turned into "(Part)(a)"
_LABEL_OK = re.compile(r"^(?:\d{1,3}|[A-Za-z]{1,2}|i{1,3}|iv|vi{0,3}|ix|xi{0,2})$", re.I)


def canon_part_no(no: str) -> str:
    """Part label -> parenthesised form: 'a' / 'a)' / 'a.' -> '(a)'; '(a) (i)' -> '(a)(i)';
    '(b)(ii)' unchanged. Anything that isn't a plain label sequence is left as-is."""
    s = (no or "").strip()
    if not s:
        return ""
    toks = [(a or b).strip() for a, b in _LABEL_TOK.findall(s)]
    toks = [t for t in toks if t]
    if not toks or not all(_LABEL_OK.match(t) for t in toks):
        return s
    return "".join(f"({t})" for t in toks)


def canon_parts_tree(nested: list[dict]) -> bool:
    """Canonicalise every `no` in a nested parts tree in place. True if anything changed."""
    changed = False
    for n in nested or []:
        no = canon_part_no(n.get("no", ""))
        if no != n.get("no"):
            n["no"], changed = no, True
        if canon_parts_tree(n.get("children") or []):
            changed = True
    return changed


_BARE_LABEL = re.compile(r"^[（(]?\s*([A-Za-z]{1,2}|\d{1,2})\s*[)）]?\s*[.、]?$")


def _bare_label(k) -> str | None:
    """Option label -> its bare form, case-folded for letters: '(b)'/'B.'/'B' -> 'B'."""
    m = _BARE_LABEL.match(str(k).strip())
    if not m:
        return None
    v = m.group(1)
    return v if v.isdigit() else v.upper()


def _opt_index(k):
    """Natural position of a raw option key (digits by value, letters by alphabet)."""
    b = _bare_label(k)
    if b is None:
        return None
    return int(b) if b.isdigit() else ord(b[0]) - 64 + (26 * (len(b) - 1))


def _remap_answer(answer, remap: dict):
    """An MCQ answer that *references* option labels follows the relabelling.
    Anything that isn't a pure label reference (a value, an expression) is untouched."""
    if not remap or not isinstance(answer, dict) or not str(answer.get("value") or "").strip():
        return answer
    toks = [t for t in re.split(r"[\s,;/、]+", str(answer["value"]).strip()) if t]
    if not toks or len(toks) > 4:
        return answer
    out = []
    for t in toks:
        b = _bare_label(t)
        if b is None or b not in remap:
            return answer
        out.append(remap[b])
    return {**answer, "value": " ".join(out)}


def canon_options(options: dict | None, answer=None):
    """MCQ options -> keys '(1)'..'(n)'. Order comes from the labels themselves when they
    are readable (A,B,C,D / 1,2,3,4), else from insertion order (= the paper's order, as
    parsed). Returns (options, answer). The answer is remapped when it names a label."""
    if not options:
        return options, answer
    keys = list(options)
    idx = [_opt_index(k) for k in keys]
    if all(i is not None for i in idx) and len(set(idx)) == len(idx):
        order = [k for _, k in sorted(zip(idx, keys), key=lambda x: x[0])]
    else:
        order = keys
    new, remap = {}, {}
    for n, k in enumerate(order, 1):
        lab = f"({n})"
        new[lab] = options[k]
        b = _bare_label(k)
        # ONLY keys that actually move go into the answer remap. Without this, a second
        # pass over already-canonical keys would build 1→(1), 2→(2)… and start reading a
        # bare numeric answer ("4") as a label — the relabelling has to be idempotent.
        if b is not None and str(k).strip() != lab:
            remap[b] = lab
    return new, _remap_answer(answer, remap)


def canon_entry(row: dict) -> bool:
    """Apply BOTH label conventions to one entry in place. True if anything changed."""
    changed = False
    if row.get("options"):
        opts, ans = canon_options(row["options"], row.get("answer"))
        if opts != row["options"]:
            row["options"], changed = opts, True
        if ans != row.get("answer"):
            row["answer"], changed = ans, True
    if canon_parts_tree(row.get("parts") or []):
        changed = True
    return changed


# ------------------------------------------------------------ interim stage files
# THE single definition of "which interim file holds the live entries for a stem".
# tagged > clean > raw, but a later stage only wins while it is at least as fresh as
# the stage it derives from — a re-run of an earlier stage supersedes a now-stale
# derivative. The dashboard bank, the entry editor and the bank check all go through this,
# so an edit can never be written into a file the bank isn't reading.

STAGE_SUFFIX = {"raw": ".jsonl", "clean": ".clean.jsonl", "tagged": ".tagged.jsonl"}
STAGE_ORDER = ("raw", "clean", "tagged")


def stage_path(ctx: context.Ctx, stem: str, stage: str) -> Path:
    return ctx.interim_dir / f"{stem}{STAGE_SUFFIX[stage]}"


def newest_stage(ctx: context.Ctx, stem: str) -> tuple[Path | None, str]:
    have = existing_stages(ctx, stem)
    if not have:
        return None, ""
    stage, use = have[0]                       # lowest existing stage is the baseline
    for nxt in STAGE_ORDER[STAGE_ORDER.index(stage) + 1:]:
        p = stage_path(ctx, stem, nxt)
        if p.is_file() and p.stat().st_mtime >= use.stat().st_mtime:
            use, stage = p, nxt
    return use, stage


def existing_stages(ctx: context.Ctx, stem: str) -> list[tuple[str, Path]]:
    """Every stage file that exists for a stem, in derivation order (raw → tagged).
    Writers MUST follow this order so the newest mtime lands on the highest stage."""
    return [(s, stage_path(ctx, stem, s)) for s in STAGE_ORDER
            if stage_path(ctx, stem, s).is_file()]


# single-char labels that are BOTH a letter and a roman numeral: label -> (letter
# predecessor, roman successor). '(i)' after '(h)' is the letter i; '(i)' before
# '(ii)' is roman one. part_depth can't see the sequence, so nest_parts resolves it.
AMBIG_ROMAN = {"i": ("h", "ii"), "v": ("u", "vi"), "x": ("w", "xi")}


def _ambiguous_single(no: str):
    groups = re.findall(r"\([^)]*\)", no or "")
    if len(groups) != 1:
        return None
    inner = groups[0].strip("() ").lower()
    return inner if inner in AMBIG_ROMAN else None


def _resolve_depth(flat: list[dict], k: int, last_top_letter: str) -> int:
    """Depth for flat[k], disambiguating single-char roman/letter labels by context."""
    no = flat[k].get("no", "")
    amb = _ambiguous_single(no)
    if not amb:
        return part_depth(no)
    prev_letter, roman_succ = AMBIG_ROMAN[amb]
    nxt = None                                   # local label of the following part
    if k + 1 < len(flat):
        nxt = local_label(flat[k + 1].get("no", "")).strip("() ").lower()
    if nxt == roman_succ:                         # (i) followed by (ii): a roman sub-list
        return 2
    if last_top_letter == prev_letter:            # (i) after (h): letters continue
        return 1
    return 2                                       # no context: keep roman default


def nest_parts(flat: list[dict]) -> list[dict]:
    """flat [{no, text}] in reading order (no may be composite like '(b)(i)') ->
    nested tree [{no(local), text, children?}] by depth. A part at depth d becomes a
    child of the nearest preceding part at depth < d."""
    root, stack = [], []                        # stack of (depth, node)
    last_top_letter = ""                         # local label of the last depth-1 part

    def place(depth, node):
        nonlocal last_top_letter
        (stack[-1][1]["children"] if stack else root).append(node)
        stack.append((depth, node))
        if depth == 1:
            last_top_letter = node["no"].strip("() ").lower()

    for k, p in enumerate(flat):
        no = p.get("no", "")
        d = _resolve_depth(flat, k, last_top_letter)
        groups = re.findall(r"\([^)]*\)", no)
        while stack and stack[-1][0] >= d:
            stack.pop()
        # synthesize missing explicit ancestors: e.g. "(a)(i)","(a)(ii)" with NO standalone
        # "(a)" — insert an empty "(a)" parent so numbering can start directly at the sub-part.
        while len(stack) < d - 1:
            lvl = len(stack) + 1
            anc = groups[lvl - 1] if lvl - 1 < len(groups) - 1 else ""
            place(lvl, {"no": anc, "text": "", "children": []})
        place(d, {"no": local_label(no), "text": p.get("text", ""),
                  **{k: p[k] for k in PART_CARRY if p.get(k) not in (None, "")},
                  "children": []})

    def prune(n):
        for c in n["children"]:
            prune(c)
        if not n["children"]:
            del n["children"]
    for n in root:
        prune(n)
    return root


def flatten_parts(nested: list[dict], prefix: str = "") -> list[dict]:
    """Nested tree -> flat [{no: composite path, text}] for LLM prompts / text scans."""
    out = []
    for p in nested:
        path = prefix + p.get("no", "")
        out.append({"no": path, "text": p.get("text", "")})
        out += flatten_parts(p.get("children", []), path)
    return out


def part_texts(nested: list[dict]) -> str:
    return " ".join(p["text"] + " " + part_texts(p.get("children", [])) for p in nested)


INLINE_PART = re.compile(r"(?m)^[ \t]*(\([a-z]{1,4}\))[ \t]+(?=\S)")


# MinerU splits a unit's exponent out of the letters: it prints "cm$^{2}$" (letters as plain
# text, the superscript in its own math span) instead of one math span. Fold them into
# "$\mathrm{cm^{2}}$" (whole unit upright in math). The split — letters OUTSIDE the $ — is the
# tell that this is a unit, not an algebraic power like "$x^{2}$" (which stays entirely in math).
def normalize_text(s: str) -> str:
    """THE single deterministic, source-agnostic text normalizer. Applied to every
    text field in BOTH segment (polish_row) and clean (_coerce) so nothing bypasses it:
    - \\cent → ¢ ; <img src> → ![]() marker
    - empty option brackets ( ) removed ; marks [3] stripped
    - answer blanks (underscores / 4+ dot-leaders / ……) → [ANSWER]  (kept for docx layout)
    Unit shapes ("cm $^{2}$" -> "$\\mathrm{cm^2}$") are NOT handled here — that judgement
    (unit vs algebraic variable) belongs to the clean step, which owns latex hygiene.
    - figure/table N.X → figure/table [QN].X  ([QN] = question-number token)
    Idempotent (running twice changes nothing)."""
    s = s or ""
    s = re.sub(r"\\cents?\b", "¢", s)
    s = re.sub(r'<img[^>]*\bsrc=["\']([^"\']+)["\'][^>]*/?>', r'![](\1)', s)
    s = TOTAL_MARK.sub("", s)                        # [Total: N] captured at row level, removed everywhere
    s = re.sub(r"[（(]\s*[)）]", "", s)             # empty mcq answer bracket
    # per-part marks [N] are NOT stripped here — captured into a field by extract_marks_tree
    s = re.sub(r"(?:\\_|_){2,}", "[ANSWER]", s)
    s = re.sub(r"\.(?:\s*\.){3,}", "[ANSWER]", s)   # 4+ dots (spaced ok), keep trailing space
    s = re.sub(r"…{2,}", "[ANSWER]", s)
    # figure/table N.X -> "Fig. [QN].X" / "Table [QN].X" ([QN] = question-number token)
    s = re.sub(r"(?i)\b(fig(?:ure)?|table)\.?\s*\d+\.(\d+)",
               lambda m: f"{'Table' if m.group(1).lower()=='table' else 'Fig.'} [QN].{m.group(2)}", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


PART_MARK = re.compile(r"\s*[\[（(]\s*(\d{1,2})\s*[\]）)]\s*$")     # trailing [3] on a part
TOTAL_MARK = re.compile(r"[\[（(]\s*(?:T|total)\b[^\]）)]*?(\d{1,3})\s*[\]）)]", re.I)   # [Total: 15]


def _pull_part_mark(text: str):
    m = PART_MARK.search(text)
    return (text[:m.start()].rstrip(), int(m.group(1))) if m else (text, None)


def extract_marks_tree(nested: list[dict]) -> None:
    """Attach a per-part `marks` field from a trailing [N] on each node's text."""
    for n in nested:
        n["text"], mk = _pull_part_mark(n["text"])
        if mk is not None:
            n["marks"] = mk
        extract_marks_tree(n.get("children", []))


# back-compat alias
polish_text = normalize_text


def split_inline_parts(flat: list[dict]) -> list[dict]:
    """A part whose text lists its own sub-parts inline (MinerU kept them in one block:
    "...:\n(i) foo\n(ii) bar") → split into an intro part + child parts with composite
    labels ((b) -> (b)(i),(b)(ii)), so nest_parts can build the tree. Needs ≥2 markers."""
    out = []
    for p in flat:
        no, text = p.get("no", ""), p.get("text", "")
        ms = list(INLINE_PART.finditer(text))
        if len(ms) < 2:
            out.append(p)
            continue
        intro = text[:ms[0].start()].strip()
        if intro:
            out.append({"no": no, "text": intro,
                        **{k: p[k] for k in PART_CARRY if p.get(k) not in (None, "")}})
        for i, m in enumerate(ms):
            end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
            seg = text[m.end():end].strip()
            if seg:
                out.append({"no": no + m.group(1), "text": seg})   # composite path
    return out


def finalize_parts(flat: list[dict]) -> list[dict]:
    """flat (LLM/segment order) → normalized, inline-split, nested tree with per-part marks."""
    norm = [{"no": canon_part_no(p.get("no", "")), "text": normalize_text(p.get("text", "")),
             **{k: p[k] for k in PART_CARRY if p.get(k) not in (None, "")}}
            for p in flat]
    norm = [p for p in norm if p["text"]]
    tree = nest_parts(split_inline_parts(norm))
    extract_marks_tree(tree)
    return tree


def split_answer_area(text: str) -> tuple[str, str | None]:
    """Lift the TRAILING answer-writing area out of a question/part text into a template.

    The area is the run of lines at the end that carry an [ANSWER] blank, so it keeps whatever
    the paper prints alongside the blank — a unit after it ("[ANSWER] cm^2"), a currency symbol
    before it ("$[ANSWER]"), or several labelled blanks ("equation: [ANSWER] / conditions:
    [ANSWER]"). Returns (text_without_area, area) with area None when there is nothing to lift.

    Only the trailing region moves. A blank in the middle of a sentence, or one followed by more
    content (a figure), stays put: it is part of the sentence, not a place to write the answer.
    Idempotent — a second pass finds no trailing blank."""
    if "[ANSWER]" not in (text or ""):
        return text, None

    def is_area_line(ln: str) -> bool:
        """A line that exists to be written on: a blank plus at most a short label or unit.
        Prose that merely contains blanks ('The value is [ANSWER] cm and …') is not one."""
        if "[ANSWER]" not in ln:
            return False
        residue = ln.replace("[ANSWER]", " ").strip()
        return len(residue.split()) <= 3

    lines = text.split("\n")
    i = len(lines)
    while i > 0:
        ln = lines[i - 1].strip()
        if is_area_line(ln):
            i -= 1
        elif not ln and i < len(lines):              # blank line INSIDE the area
            i -= 1
        else:
            break
    if i >= len(lines):                              # last line is not an answer line
        return text, None
    area = "\n".join(lines[i:]).strip()
    body = "\n".join(lines[:i]).rstrip()
    return body, (area or None)


_ANS_MARK = re.compile(r"(?:\(\s*[A-Za-z]{1,3}\s*\)|\(\s*\d{1,2}\s*\)){1,3}")


def has_answer(entry: dict, field: str = "answer") -> bool:
    """True when the entry carries `field` at entry level or on any part (answers move onto
    the parts as soon as a question has sub-parts)."""
    if (entry.get(field) if field == "solution" else (entry.get("answer") or {}).get("value")):
        return True

    def walk(ps):
        return any(p.get(field) or walk(p.get("children") or []) for p in ps or [])

    return walk(entry.get("parts"))


def part_paths(parts: list, pre: str = ""):
    """Canonical full paths of every part, in order: '(a)', '(b)', '(b)(i)', …"""
    for p in parts or []:
        key = pre + (p.get("no") or "")
        yield key
        yield from part_paths(p.get("children") or [], key)


def split_by_parts(text: str, labels: list) -> tuple[dict, str]:
    """Packed answer/solution -> ({part_path: text}, leading_text).

    An answer key writes one line per question — "(a) $6:11$; (b) Tank X: 31.2 l" — so the
    per-part structure is present in the string but unusable by anything downstream. This
    cuts it at the printed part markers, mapping a bare "(i)" onto the "(b)" it follows.
    Returns ({}, text) when the blob is not a labelled list, which leaves it untouched.

    The result is an exact partition of `text`: only the markers themselves and the
    separators between entries are dropped."""
    if not text or not labels:
        return {}, text
    known, tops = set(labels), {l for l in labels if l.count("(") == 1}
    hits, parent = [], ""
    for m in _ANS_MARK.finditer(text):
        toks = ["(" + t.strip().lower() + ")"
                for t in re.findall(r"\(\s*([A-Za-z]{1,3}|\d{1,2})\s*\)", m.group(0))]
        path = "".join(toks)
        if path in known:
            if len(toks) == 1 and path in tops:
                parent = path
            hits.append((m.start(), m.end(), path))
        elif len(toks) == 1 and parent and (parent + path) in known:
            hits.append((m.start(), m.end(), parent + path))
    # one marker suffices when the text STARTS with it ("(b) 5"); otherwise require two, so
    # prose that merely mentions "(a)" is not mistaken for a labelled list.
    if not hits or (len(hits) < 2 and hits[0][0] != 0):
        return {}, text
    out = {}
    for k, (_s, e, path) in enumerate(hits):
        end = hits[k + 1][0] if k + 1 < len(hits) else len(text)
        val = text[e:end].strip().strip(";").strip()
        if val:
            out[path] = val
    return out, text[:hits[0][0]].strip(" ;,\n")


def apply_part_answers(row: dict) -> None:
    """Move a packed answer/solution onto the parts it names.

    An answer belongs to whatever it actually answers: a question with sub-parts keeps its
    answers on the parts and holds NOTHING at entry level, so there is exactly one place to
    read or edit each one. A question with no sub-parts keeps its answer where it is.

    The exception is an answer that names no part at all ("$1.60" on a 3-part question): it
    answers the question as a whole and cannot be attributed to any part, so it stays at
    entry level rather than being dropped."""
    labels = list(part_paths(row.get("parts") or []))
    if len(labels) < 2:
        return
    by_path = {}

    def index(parts, pre=""):
        for p in parts or []:
            key = pre + (p.get("no") or "")
            by_path[key] = p
            index(p.get("children") or [], key)

    index(row.get("parts") or [])

    for field in ("answer", "solution"):
        cur = ((row.get("answer") or {}).get("value") if field == "answer"
               else row.get("solution")) or ""
        have = {p: by_path[p][field] for p in labels
                if p in by_path and by_path[p].get(field)}
        if have:
            lead = ""                                   # parts win; entry is derived
        else:
            have, lead = split_by_parts(cur, labels)
            for path, val in have.items():
                if path in by_path:
                    by_path[path][field] = val
        if not have:
            continue                                    # names no part -> leave it on the entry
        if field == "answer":
            row["answer"] = ({"value": lead, "kind": (row.get("answer") or {}).get("kind", "human")}
                             if lead else None)
        else:
            row["solution"] = lead or None


def apply_answer_area(row: dict) -> None:
    """Move every trailing answer area (stem + each part, recursively) into `answer_area`."""
    row["stem"], area = split_answer_area(row.get("stem") or "")
    row["answer_area"] = area

    def walk(parts):
        for p in parts:
            p["text"], a = split_answer_area(p.get("text") or "")
            if a:
                p["answer_area"] = a
            walk(p.get("children") or [])

    walk(row.get("parts") or [])


def polish_row(row: dict) -> None:
    flat_in = flatten_parts(row["parts"]) if row["parts"] and "children" in str(row["parts"]) else row["parts"]
    # total marks: explicit "[Total: N]" anywhere (raw text), else a lone trailing "[N]"
    # on a no-parts stem. normalize_text removes [Total: N] from every field.
    blob = row["stem"] + " " + " ".join(p.get("text", "") for p in flat_in)
    tm = TOTAL_MARK.search(blob)
    total = int(tm.group(1)) if tm else None
    row["stem"] = normalize_text(row["stem"])
    parts = finalize_parts(flat_in)
    if not parts:                                    # no-parts question: stem-trailing [N] = marks
        row["stem"], smk = _pull_part_mark(row["stem"])
        if smk is not None and total is None:
            total = smk
    if total is not None:
        row["meta"]["marks"] = total
    if len(flatten_parts(parts)) < len([p for p in flat_in if p.get("text")]):
        row["flags"].append("empty_parts_removed")
    row["parts"] = parts
    if row.get("options"):
        row["options"] = {k: normalize_text(v).strip() for k, v in row["options"].items()}
    canon_entry(row)                                 # "(1)" option keys, "(a)" part labels
    apply_answer_area(row)                           # trailing blanks -> answer_area field
    apply_part_answers(row)                          # packed "(a) …; (b) …" -> per-part answers
    row["meta"]["schema"] = SCHEMA_VERSION


def validate(entry: dict, img_dir: Path) -> None:
    f = entry["flags"]
    text_all = entry["stem"] + part_texts(entry["parts"]) + (entry["solution"] or "")
    if not latex_ok(text_all):
        f.append("latex_suspect")
    if len(entry["stem"]) < 6 and not entry["parts"]:
        f.append("short_stem")
    for a in entry["imgs"]:
        # solution images (src="ans") live in the sibling <stem>_ans/ extraction
        d = img_dir.with_name(img_dir.name + "_ans") if a.get("src") == "ans" else img_dir
        if a["kind"] == "image" and not (d / a["path"]).is_file():
            f.append("image_missing")
    if entry.get("meta", {}).get("unknown_block_types"):
        f.append("unknown_block_type")
    # answers live on the parts once a question has sub-parts, so "answered" must be judged
    # there too — otherwise every structured question is flagged as missing an answer.
    def _any_part(field):
        def walk(ps):
            return any(p.get(field) or walk(p.get("children") or []) for p in ps or [])
        return walk(entry.get("parts"))

    if entry["solution"] is None and not _any_part("solution"):
        f.append("no_solution")
    if entry["answer"] is None and not _any_part("answer"):
        f.append("no_answer")
