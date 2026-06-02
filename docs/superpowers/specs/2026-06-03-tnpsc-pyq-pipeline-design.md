# TNPSC PYQ Pipeline — Design Spec
**Date:** 2026-06-03

---

## Overview

Pipeline to extract TNPSC Group exam PYQs from scanned PDFs using Claude Sonnet (Anthropic Vision), store as structured JSON per PDF, merge into a combined master, and present in a filterable bilingual HTML study tool with quiz mode.

Key differences from UPSC pipeline:
- **Bilingual**: English + Tamil within each question (General Studies papers)
- **Language-only papers**: General English (EN) and General Tamil (TA) are separate papers with different questions
- **Answer key embedded**: tick mark in same question PDF — no separate answer import step
- **5 options A–E** (not A–D)
- **6 question types** including `other` catch-all
- **Syllabus-mapped tagging**: Unit → Theme → Topic hierarchy from `tnpsc_general_studies_aptitude_mental_ability_syllabus.json`

---

## File Structure

```
TNPSC_Prelims_PYQs/
├── pdf/
│   ├── G1/
│   │   ├── TNPSC_Group1_2025_GeneralStudies.pdf
│   │   └── TNPSC_Group1A_2023_GeneralStudies.pdf
│   ├── G2/
│   │   ├── TNPSC_Group2_2024_GeneralStudies.pdf
│   │   ├── TNPSC_Group2_2024_GeneralStudies_English.pdf
│   │   └── TNPSC_Group2_2024_GeneralStudies_Tamil.pdf
│   └── G4/
│       └── ...
├── images/
│   └── G1/2025/gs/
│       ├── TNPSC_G1_2025_gs_p001.png
│       └── TNPSC_G1_2025_gs_p002.png
├── data/
│   ├── G1_2025_gs_partial.json       ← written per-page during extraction
│   ├── G1_2025_gs.json               ← finalized when all pages done
│   ├── G2_2024_en.json
│   └── G2_2024_ta.json
├── state/
│   └── G1_2025_gs_progress.json
├── master.json                        ← merged, never edit directly
├── syllabus.json                      ← copy of tnpsc_general_studies_aptitude_mental_ability_syllabus.json
├── tnpsc_pyqs.html
│
├── prompt_extract.txt                 ← Claude extraction prompt (Pass 1)
├── prompt_reextract.txt               ← Claude re-extraction prompt (Pass 2 — for "other" types)
├── extract.py
├── reextract.py
├── merge.py
└── build_html.py
```

**Rule:** never edit `master.json` directly. Source = per-PDF data JSONs. Run `merge.py` to regenerate.

---

## Paper Types and Language Modes

| Paper type suffix | Language mode | Description |
|---|---|---|
| `_gs` | `bilingual` | General Studies — EN + TA in one PDF |
| `_en` | `en_only` | General English — English only |
| `_ta` | `ta_only` | Tamil only |

Each PDF produces one JSON file. `merge.py` combines all.

---

## JSON Schema

### Per-file JSON wrapper

```json
{
  "group": "G1",
  "subgroup": null,
  "year": 2025,
  "paper_type": "gs",
  "language_mode": "bilingual",
  "total_questions": 200,
  "paper_code": "CCSEI-IAP25",
  "questions": [ ...array of question objects... ]
}
```

`subgroup`: string or null — for variants like `"1A"`, `"1B_1C"`.

### Per-question object (top-level fields)

```json
{
  "id":              "tnpsc_g1_2025_gs_001",
  "question_no":     1,
  "question_type":   "statement_mcq",

  "unit_id":         1,
  "unit":            "General Science",
  "theme":           "Chemistry",
  "topic":           "Elements and Compounds",

  "disputed":        false,
  "stem_has_blank":  false,
  "has_math":        false,
  "math_format":     null,
  "options_layout":  "single_column",

  "media":           null,
  "en":              { ...language block... },
  "ta":              { ...language block... },
  "answer_key":      "C",
  "meta":            { ...meta block... }
}
```

**`id` format:** `tnpsc_{group}_{year}_{paper_type}_{zero-padded-number}`
Examples: `tnpsc_g1_2025_gs_001`, `tnpsc_g2_2024_en_042`, `tnpsc_g4_2022_ta_100`

**`answer_key`:** `"A"–"E"` | `"X"` (omitted/disputed) | `null` (not yet extracted)

For `en_only` papers: `ta: null`. For `ta_only` papers: `en: null`.

### Question types enum

```
"simple_mcq"
"statement_mcq"
"match_the_following"
"assertion_reason"
"chronological_order"
"other"
```

### Language block — by question type

**`simple_mcq`:**
```json
{
  "stem": "string",
  "question_keywords": ["term1", "term2"],
  "options": [{ "key": "A", "text": "..." }, ...]
}
```

**`statement_mcq`:**
```json
{
  "stem": "string",
  "question_keywords": ["term1", "term2"],
  "item_numbering_style": "roman | arabic | alpha",
  "statements": [{ "no": "i", "text": "..." }],
  "options": [{ "key": "A", "text": "..." }, ...]
}
```

**`match_the_following` (tabular):**
```json
{
  "stem": "string",
  "question_keywords": ["term1", "term2"],
  "match_style": "tabular",
  "left_column": [{ "key": "a", "text": "..." }],
  "right_column": [{ "no": 1, "text": "..." }],
  "options": [
    { "key": "A", "mapping": { "a": 5, "b": 4, "c": 1, "d": 3 } },
    { "key": "E", "text": "Answer not known" }
  ]
}
```

**`match_the_following` (inline):**
```json
{
  "stem": "string",
  "question_keywords": ["term1"],
  "match_style": "inline",
  "items": [{ "no": "1", "text": "full pair text" }],
  "options": [{ "key": "A", "text": "1 and 3 are correct" }, ...]
}
```

**`assertion_reason`:**
```json
{
  "assertion": "string",
  "reason": "string",
  "question_keywords": ["term1", "term2"],
  "options": [{ "key": "A", "text": "verbatim option text" }, ...]
}
```

**`chronological_order`:**
```json
{
  "stem": "string",
  "question_keywords": ["term1"],
  "item_numbering_style": "roman | arabic",
  "items": [{ "no": "1", "text": "..." }],
  "options": [{ "key": "A", "text": "..." }, ...]
}
```

**`other` (catch-all):**
```json
{
  "raw_content": "complete verbatim text of the question, word for word"
}
```

### `question_keywords` rule

Prompt instruction: *"Extract 2–6 key terms exactly as they appear in the text — proper nouns, technical terms, scheme names, article numbers, named acts, named persons. Do not paraphrase or generalise."*

Both `en` and `ta` blocks carry their own `question_keywords` drawn from their respective language text.

### Meta block

```json
{
  "extraction_status": "pending | extracted | verified",
  "ocr_confidence":    0.95,
  "image_ref":         "images/G1/2025/gs/TNPSC_G1_2025_gs_p003.png",
  "notes":             ""
}
```

`image_ref` is the exact path to the page image this question was extracted from — used by `reextract.py` for targeted re-extraction.

### State file (`state/G1_2025_gs_progress.json`)

```json
{
  "group": "G1",
  "year": 2025,
  "paper_type": "gs",
  "total_pages": 160,
  "processed_pages": [1, 2, 3],
  "failed_pages": [],
  "last_updated": "2026-06-03T10:00:00"
}
```

### `master.json`

```json
{
  "total_questions": 3500,
  "questions": [ ...flat array sorted by group, year, paper_type, question_no... ]
}
```

---

## Extraction Pipeline (`extract.py`)

### Usage
```
python extract.py pdf/G1/TNPSC_Group1_2025_GeneralStudies.pdf --group G1 --year 2025 --paper-type gs
python extract.py pdf/G1/TNPSC_Group1_2025_GeneralStudies.pdf --group G1 --year 2025 --paper-type gs --retry-failed
```

### Flow

1. Parse `--group`, `--year`, `--paper-type` → derive `language_mode`
2. Load or create `state/G1_2025_gs_progress.json`
3. Convert PDF pages → images → `images/G1/2025/gs/` (skip if image already exists)
4. For each page (in order):
   - Already in `processed_pages`? → skip
   - Send image + `prompt_extract.txt` to Claude Sonnet (Anthropic Vision API)
   - Strip any non-JSON wrapper; parse JSON
   - If parse fails → retry once → if still fails → log to `failed_pages`, continue
   - Enrich each question: add `id`, `group`, `year`, `paper_type`, `meta.image_ref`
   - Append questions to `data/G1_2025_gs_partial.json`
   - Mark page done in state file immediately after each page
5. All pages done → validate: collect all question numbers, report gaps
6. Rename `G1_2025_gs_partial.json` → `G1_2025_gs.json`
7. Print summary: questions extracted, pages processed, failed pages, missing numbers

### Completeness guarantee in `prompt_extract.txt`

The prompt includes a hard rule block:

```
COMPLETENESS RULE — NON-NEGOTIABLE:
- Capture EVERY word, number, symbol, punctuation, and letter exactly as printed.
- Do NOT summarise, shorten, paraphrase, or rephrase any text.
- Do NOT skip any part of the stem, statements, items, or options.
- If a word/character is unclear or smudged, write [UNCLEAR] — do NOT omit it.
- If the question does not match any known type, use type "other" and capture all
  text verbatim in raw_content. Missing even one word is an extraction failure.
```

### Answer key extraction

The prompt also instructs:
```
- Identify the ticked/circled option. Set answer_key to that option key (A/B/C/D/E).
- If no option is ticked or the question is marked omitted/under review/discarded,
  set answer_key to "X".
- If you cannot determine the answer, set answer_key to null.
```

---

## Re-extraction Pipeline (`reextract.py`)

### Purpose

Targeted re-extraction for questions where `question_type == "other"` after a new type is defined, or to fix failed pages.

### Usage
```
python reextract.py --json data/G1_2025_gs.json --type other
python reextract.py --image images/G1/2025/gs/TNPSC_G1_2025_gs_p003.png --json data/G1_2025_gs.json
```

### Flow (Pass 2 — type proposal mode)

1. `--type other`: collect all `"other"` records from the JSON, group by `image_ref`
2. For each unique image:
   - Send image + `prompt_reextract.txt` to Claude:
     ```
     This image contains a question classified as "other" (unknown structure).
     Analyse its structure. Propose:
       1. A new question type name (snake_case)
       2. A JSON language-block schema in the same style as existing types
       3. A fully extracted question object using that structure
     Return: { "proposed_type": "...", "schema": {...}, "extracted": {...} }
     ```
   - Print proposal to terminal for human review
3. Human approves/edits the type name and schema
4. `--patch`: add new type to `prompt_extract.txt` enum, patch records in JSON, update `extraction_status` to `"extracted"`

### Flow (image re-extraction mode)

1. `--image <path>`: send that image with updated `prompt_extract.txt`
2. Parse response → find matching question numbers in JSON → overwrite only those records
3. Leave all other questions untouched

---

## Merge (`merge.py`)

```
python merge.py
```

- Reads all `data/*.json` (ignores `*_partial.json`)
- Sorts by group order (G1, G2, G4...), then year, then paper_type, then question_no
- Flags duplicate IDs across files
- Writes `master.json`
- Safe to re-run anytime

---

## HTML Generation (`build_html.py`)

```
python build_html.py
```

- Reads `master.json` + `syllabus.json`
- Injects as `__QUESTIONS_DATA__` and `__SYLLABUS_DATA__` into HTML template
- Outputs `tnpsc_pyqs.html` — single static file, all logic client-side

---

## HTML Features

### Language Toggle (top bar, always visible)

```
[ English ]  [ Tamil ]  [ Both ]
```

- **English**: renders `en` block only
- **Tamil**: renders `ta` block only
- **Both** (default): stacks EN then TA within each card
- For language-only papers (`en_only`/`ta_only`): absent block silently omitted

### Filters (6 cards)

| Card | Select mode | Default | Notes |
|---|---|---|---|
| **Group** | Multi-select | G1 only | Buttons per group; "Deselect all" toggle |
| **Paper Type** | Single-select | GS | Switching deselects previous |
| **Year** | Multi-select | Latest year only | Buttons per available year |
| **Subject (Unit)** | Single-select | None (all shown) | 8 TNPSC units from syllabus.json |
| **Keyword** | Text input | — | Searches `en.question_keywords` or `ta.question_keywords` per active language |
| **Quiz** | — | — | Shows filtered count + Start Quiz button |

All active filters combine with AND logic. Live count updates on every change.

### Browse Mode (default)

Questions as cards, grouped by Unit, 2-column grid.

### Question Card — per type

| Type | Render |
|---|---|
| `simple_mcq` | stem + options |
| `statement_mcq` | stem → numbered statements (roman/arabic/alpha) → options |
| `match_the_following` tabular | stem → two-column table (a/b/c/d ↔ 1/2/3/4/5) → mapping options A–D + E text |
| `match_the_following` inline | stem → numbered pair list → text options |
| `assertion_reason` | `Assertion [A]:` block + `Reason [R]:` block → verbatim options |
| `chronological_order` | stem → numbered items → options |
| `other` | `raw_content` verbatim in monospace block + yellow `⚠ UNCLASSIFIED` badge |

**Card badges:**
- `disputed: true` → red `DISPUTED` badge
- `answer_key: "X"` → grey `OMITTED` instead of answer reveal
- `has_math: true` → MathJax rendering
- `media` present → image injected at `placement` point
- `stem_has_blank: true` → blank rendered as styled underline

**Options:** up to 5 (A–E); two-column layout if all options ≤ 45 chars, else single-column.

### Subject Overview Table

Collapsible, hidden by default. Columns: Unit name + one per active year + Total. Cell click filters to that unit + year.

### Quiz Mode

**Entry:** "Start Quiz" button (disabled if no valid `answer_key` in selection).

**One-at-a-time:**
- Single card, answers hidden
- Click option → immediate correct/wrong feedback (✓/✗)
- Score tracker: `Q 7 of 34 — Score: 5/6`
- Next → end screen → option to review wrong answers only

**Exam mode:**
- All filtered questions shown, answers hidden
- Click to mark selection (highlighted orange)
- Submit → all answers revealed, score shown
- Questions with `answer_key: "X"` or `null` excluded from quiz pool

---

## CLI Run Order

```bash
# Extract one PDF
python extract.py pdf/G1/TNPSC_Group1_2025_GeneralStudies.pdf --group G1 --year 2025 --paper-type gs

# Retry failed pages
python extract.py pdf/G1/TNPSC_Group1_2025_GeneralStudies.pdf --group G1 --year 2025 --paper-type gs --retry-failed

# Review and re-extract "other" type questions
python reextract.py --json data/G1_2025_gs.json --type other

# After all desired PDFs extracted
python merge.py
python build_html.py
```

---

## Tagging — Syllabus Mapping

The extraction prompt includes the full `syllabus.json` content and instructs:

```
TAG each question with:
  unit_id:  integer 1–8 matching the unit in the provided syllabus
  unit:     exact unit name string
  theme:    exact theme name within that unit
  topic:    exact topic_name within that theme

If no exact topic match, use the nearest unit and set topic to "Miscellaneous".
Do not invent unit names or theme names not in the syllabus.
```

---

## Deployment

Local use during extraction. Static files for Vercel when frozen:
- `tnpsc_pyqs.html`
- `master.json`
- `syllabus.json`

No backend required. All filtering/quiz logic runs client-side in JS.
