# Interim schema (the question-bank contract)

`interim/*.jsonl` is the **product** of this project — the editable source of truth. The SQLite
DB, the docx export and the dashboard are all rebuildable views over it. This file is the
normative contract for a row: what fields exist, what values are legal, and which conventions
downstream code may rely on.

Everything here is enforced or checked in `interim_build.py` (`canon_entry`, `validate`).
If code and this document disagree, that is a bug in one of them — say which.

- **Status:** v2 draft (adds `answer_area`). v1 = everything shipped before, implicit/unversioned.
- **Version marker:** `meta.schema` (int). Absent ⇒ v1.

---

## 1. Row

One JSON object per line, UTF-8, no trailing whitespace.

| field | type | req | notes |
|---|---|:--:|---|
| `qid` | string | ✓ | `<stem>-<NNN>`, unique **within a source** (not globally) |
| `kind` | `"mcq"` \| `"question"` | ✓ | `mcq` iff `options` is non-empty |
| `stem` | string | ✓ | may be `""` — a paper can start straight at `(a)` |
| `parts` | array\<Part\> | ✓ | `[]` when the question has no sub-parts |
| `options` | object \| null | ✓ | MCQ options, keys canonicalised (§3.1) |
| `answer` | object \| null | ✓ | `{value: string, kind: string}` |
| `answer_area` | string \| null | — | **v2**, see §4 |
| `solution` | string \| null | ✓ | worked solution, free text |
| `imgs` | array\<Img\> | ✓ | provenance for every referenced image |
| `meta` | object | ✓ | §2 |
| `flags` | array\<string\> | ✓ | review/QA markers, never load-bearing for parsing |
| `tags` | object | — | added by the tagging step: `{topic:[], type, difficulty}` |

**Part**

| field | type | req | notes |
|---|---|:--:|---|
| `no` | string | ✓ | canonical label, §3.2 |
| `text` | string | ✓ | |
| `marks` | number | — | printed marks for this part |
| `answer_area` | string \| null | — | **v2**, §4 |
| `children` | array\<Part\> | — | nested sub-parts; absent when none |

**Img**: `{kind, path, src, page, bbox}` — `src` is `"q"` (question PDF) or `"ans"` (answer PDF);
`path` is relative to that source's extraction dir.

## 2. `meta`

`source_id`, `subject`, `stage`, `level` (source coordinates) · `file` (originating `raw/*.pdf`) ·
`pages` (int array) · `qno` (printed question number, string) · `section` (heading, `""` if none) ·
`blocks` (`[first, last]` MinerU block indices — the provenance range) · `marks` (question total) ·
`edited_at` (set on human edit) · `schema` (v2+).

## 3. Format conventions (normative)

### 3.1 MCQ options
Keys are **`(1)`, `(2)`, `(3)`, …** in the paper's printed order. Source labels (`A/B/C/D`,
`1./2./3.`) are normalised on ingest by `canon_options`, which also remaps `answer.value` when it
names an option. Rendering style (letters, dots, bare) is an **export** choice, never stored.

### 3.2 Sub-part labels
Parenthesised, one token per level, nested by depth:

| depth | form | example |
|---|---|---|
| 1 | `(a)`, `(b)`, … | `(a)` |
| 2 | `(i)`, `(ii)`, … | `(i)` |
| 3 | `(1)`, `(2)`, … | `(1)` |

`no` holds the **local** label; the tree carries the hierarchy (`children`). Composite input such
as `(b)(i)` is accepted on ingest and split into depth by `nest_parts`. A parent may exist with
empty `text` when the paper prints no intro line for it.

### 3.3 Marks
A field (`part.marks`, `meta.marks`), **not** text. The printed `[N]` / `[Total: N]` is consumed on
ingest and must not remain in `text`/`stem`.

### 3.4 Math, tables, images
- Math stays **verbatim** as MinerU emitted it: `$…$` inline, `$$…$$` display. Downstream never
  rewrites it. Chemistry may use mhchem `\ce{…}`.
- Tables stay **HTML** (`<table>…</table>`). Never converted to markdown pipes.
- Images are referenced inline as `![](<path>)` and must have a matching entry in `imgs`.

### 3.5 Placeholder vocabulary
Closed set. Anything else in `[BRACKETS]` is literal content.

| token | meaning | where |
|---|---|---|
| `[ANSWER]` | the answer-writing placeholder | `answer_area` (§4) |
| `[QN]` | cross-reference to this question's displayed number | inline in text |

## 4. `answer_area` (v2)

The space a student writes the answer in, as **structured data** rather than prose or layout.

- Lives on the **entry** (question-level answer) and on **each part** that expects a written answer.
- Value is a short **template string**: the literal `[ANSWER]` plus, if the paper prints one, a
  unit or symbol.
- `null` / absent ⇒ this question or part has no answer-writing area.

```jsonc
"answer_area": "[ANSWER]"          // plain
"answer_area": "[ANSWER] cm^2"     // unit printed after the blank
"answer_area": "[ANSWER] %"        // symbol
"answer_area": null                // none (e.g. a "Draw…" part)
```

**Scope decisions (locked):**
- The field is the **only** representation. Inline `[ANSWER]` inside `stem`/`text` is **not** part
  of v2 — the ingest moves it out into the field.
- The bank does **not** model answer *lines* (count, length, ruling). That is presentation, decided
  at export time from `marks` and question type.
- Units live **inside** the template string, not in a separate field.

## 5. Segmentation roles (intermediate, not persisted)

`llm_segment` labels each MinerU block with one role. These never reach the bank; they exist only
to drive `assemble_questions`.

`section` · `q` · `body` · `part` · `option` · `figure` · `solution` · `noise`

Two vocabularies are in play and must not be conflated:

- **MinerU `type`** — *structural*: what the block is (`text`, `equation`, `image`, `table`,
  `chart`, `code`, plus noise types). Given by the extractor.
- **role** — *functional*: what the block does in the paper. Decided by the labeler.

A table is `type: "table"`; its role may be `figure`, `option` or `body` depending on function.
There is deliberately **no `table` role** — that would mix structure into the semantic vocabulary
and duplicate `type`.

## 6. Invariants

1. **Nothing is silently dropped.** Every non-noise block lands in some entry, or the run reports
   it (`blocks_dropped` / `warnings` in `*.report.json`).
2. **Never crash on drift.** Unknown block types degrade to text and raise a flag.
3. **`flags` are advisory.** Parsing must never depend on them.
4. **Provenance is preserved.** `meta.blocks` + `imgs[].bbox` must always allow re-cropping from
   the original PDF.
5. **jsonl is the source of truth.** The DB and any index are rebuildable.
