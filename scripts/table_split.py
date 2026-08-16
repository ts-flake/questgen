"""Split exam tables that are page LAYOUT into per-question blocks.

Some papers typeset whole questions inside one table: the question number sits in a narrow
left column, the stem in a wide colspan cell, and the option/data rows follow. MinerU
faithfully returns that as a single `table` block — one block holding many questions — and
the labeler then has to assign one role to all of them (it also only ever sees a short
preview of a table, so it cannot even read them). The result is every question collapsing
into a single entry.

This pass runs before labeling and rewrites such a block into the ordinary block stream:
one `q` text block per question plus its data/option blocks. Everything downstream —
labeler, assembler — keeps working on plain blocks and needs no notion of tables.

Only the split is decided here, because only the split is unambiguous (a numbered row
starts a question). What a segment's rows *mean* varies too much to hardcode: A–D rows may
be the options themselves (each carrying data columns), or plain data with the options
printed as inline text, or the options may live in separate figure blocks entirely. So a
segment's non-stem rows are handed on as content and the labeler/clean step decides.
"""
from __future__ import annotations

import re

# A cell that is only a question number: "1", "1.", "12)" …
_QNO = re.compile(r"^\s*(\d{1,2})\s*[.)]?\s*$")


def parse_grid(html: str) -> list[list[dict]]:
    """[[cell dicts]] per row, with colspan/rowspan (th treated like td)."""
    out = []
    for rh in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", html or ""):
        cells = []
        for m in re.finditer(r"(?is)<t[dh]([^>]*)>(.*?)</t[dh]>", rh):
            attrs, content = m.group(1), m.group(2)
            sp = lambda a: int((re.search(rf'{a}\s*=\s*"?(\d+)', attrs, re.I) or [0, "1"])[1])
            cells.append({"content": content, "colspan": max(1, sp("colspan")),
                          "rowspan": max(1, sp("rowspan"))})
        if cells:
            out.append(cells)
    return out


def cell_text(cell: dict) -> str:
    """Visible text of a cell (tags stripped, whitespace collapsed)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell.get("content", ""))).strip()


def _row_text(row: list[dict]) -> str:
    return " ".join(t for t in (cell_text(c) for c in row) if t).strip()


def _width(rows: list[list[dict]]) -> int:
    return max((sum(c["colspan"] for c in r) for r in rows), default=0)


def _is_qno_row(row: list[dict], width: int) -> str | None:
    """Question number if this row starts one: a lone-number first cell followed by a cell
    spanning (nearly) the whole table — the signature of a stem typeset across the row.

    The width test is what separates a layout table from a data table whose first column
    happens to be numeric ("1 | 12 | 12 | 10" must NOT read as question 1)."""
    if len(row) < 2:
        return None
    m = _QNO.match(cell_text(row[0]))
    if not m or not _row_text(row[1:]):
        return None
    if not any(c["colspan"] >= max(2, width - 1) for c in row[1:]):
        return None
    return m.group(1)


def _render(rows: list[list[dict]]) -> str:
    """Rows back to an HTML table (spans preserved), so downstream sees a normal table."""
    out = ["<table>"]
    for r in rows:
        out.append("<tr>")
        for c in r:
            attrs = ""
            if c["colspan"] > 1:
                attrs += f' colspan="{c["colspan"]}"'
            if c["rowspan"] > 1:
                attrs += f' rowspan="{c["rowspan"]}"'
            out.append(f"<td{attrs}>{c['content']}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _segments(rows: list[list[dict]], width: int) -> list[tuple[str | None, list[list[dict]]]]:
    """[(qno|None, rows)] — rows before the first numbered row keep qno None."""
    segs: list[tuple[str | None, list[list[dict]]]] = []
    cur_no, cur = None, []
    for r in rows:
        no = _is_qno_row(r, width)
        if no is not None:
            if cur:
                segs.append((cur_no, cur))
            cur_no, cur = no, [r]
        else:
            cur.append(r)
    if cur:
        segs.append((cur_no, cur))
    return segs


def _split_segment(no: str, rows: list[list[dict]], width: int) -> list[dict]:
    """One question's rows -> [stem text block] + [remaining rows as a table block].

    The stem is the wide cell on the numbered row (and any later full-width prose rows);
    narrow rows are data/options and stay a table for the labeler to judge."""
    stem_parts, rest = [], []
    for k, r in enumerate(rows):
        body = r[1:] if k == 0 else r                      # drop the number cell itself
        if not _row_text(body):
            continue                                        # spacer row
        wide = len(body) == 1 and (body[0]["colspan"] >= max(2, width - 1))
        if wide:
            stem_parts.append(cell_text(body[0]))
        else:
            rest.append(r)
    out = [{"type": "text", "_text": f"{no}. " + " ".join(stem_parts).strip(),
            "_role": "q", "_label": no}]
    if rest:
        # rows whose first column is mostly a bare A/B/C/D (or 1-4) are the option list;
        # anything else is data for the question. Hinting both means the split output never
        # goes back through the labeler, which can only see a truncated preview of a table.
        firsts = [cell_text(r[0]) for r in rest if r]
        keys = [t for t in firsts if re.fullmatch(r"[A-H]|[1-9]", t)]
        role = "option" if len(keys) >= 2 and len(keys) >= len([f for f in firsts if f]) - 1 else "figure"
        out.append({"type": "table", "table_body": _render(rest), "_text": "", "_role": role})
    return out


def split_block(block: dict) -> list[dict] | None:
    """A layout table -> replacement blocks. None if this is a plain data table (no row
    starts a question), in which case it is left exactly as it was."""
    rows = parse_grid(block.get("table_body") or "")
    if not rows:
        return None
    width = _width(rows)
    segs = _segments(rows, width)
    if not any(no is not None for no, _ in segs):           # a plain data table: leave alone
        return None
    out: list[dict] = []
    for no, seg_rows in segs:
        if no is None:                                      # leading rows: keep as a table
            if _row_text([c for r in seg_rows for c in r]):
                out.append({"type": "table", "table_body": _render(seg_rows), "_text": ""})
            continue
        out.extend(_split_segment(no, seg_rows, width))
    for b in out:                                           # carry provenance from the original
        for k in ("page_idx", "bbox", "img_path"):
            b.setdefault(k, block.get(k, "" if k == "img_path" else None))
        b["_from_table"] = True
    return out or None


def split_tables(blocks: list[dict]) -> list[dict]:
    """Rewrite layout tables in a block stream. Indices (`_i`) are reassigned over the
    result; `_src_i` keeps the original block index so provenance survives."""
    out: list[dict] = []
    for b in blocks:
        parts = split_block(b) if b.get("type") == "table" else None
        for nb in (parts or [b]):
            if parts:
                nb["_src_i"] = b.get("_i")
            out.append(nb)
    for i, b in enumerate(out):
        b.setdefault("_src_i", b.get("_i"))
        b["_i"] = i
        b.setdefault("_text", b.get("text") or "")
    return out
