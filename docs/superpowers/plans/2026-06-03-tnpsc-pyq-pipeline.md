# TNPSC PYQ Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete pipeline that extracts TNPSC group exam PYQs from scanned PDFs using Claude Vision, stores them as bilingual (EN+TA) structured JSON, and presents them in a single-file filterable HTML study tool with quiz mode.

**Architecture:** Four Python scripts (`extract.py`, `reextract.py`, `merge.py`, `build_html.py`) share utilities from `lib/`. `extract.py` converts PDF pages to images, sends each to Claude Sonnet, and writes one JSON per PDF. `build_html.py` injects `master.json` into a self-contained HTML template. All filtering and quiz logic runs client-side in vanilla JS.

**Tech Stack:** Python 3.10+, `anthropic` SDK, `pdf2image` + `Pillow`, `pytest`, vanilla HTML/CSS/JS (no frameworks)

**Spec:** `docs/superpowers/specs/2026-06-03-tnpsc-pyq-pipeline-design.md`

---

## File Map

| File | Role |
|---|---|
| `requirements.txt` | Python dependencies |
| `lib/__init__.py` | Empty package marker |
| `lib/pdf_utils.py` | PDF → PNG images per page |
| `lib/api_client.py` | Claude Vision API call + retry |
| `lib/schema_utils.py` | ID generation, question enrichment, state I/O |
| `lib/syllabus_utils.py` | Load syllabus, build compact tagging reference |
| `prompt_extract.txt` | Pass 1 prompt template (placeholders: `{LANGUAGE_MODE}`, `{PAPER_INFO}`, `{SYLLABUS_COMPACT}`) |
| `prompt_reextract.txt` | Pass 2 prompt for proposing new question types from `other` records |
| `extract.py` | CLI: PDF → per-PDF JSON with resume support |
| `reextract.py` | CLI: targeted re-extraction of `other` questions or failed pages |
| `merge.py` | CLI: all data JSONs → `master.json` |
| `lib/template.html` | HTML/CSS/JS template with `__QUESTIONS_DATA__` and `__SYLLABUS_DATA__` placeholders |
| `build_html.py` | Injects data into template → `tnpsc_pyqs.html` |
| `tests/test_schema_utils.py` | Unit tests for ID generation and enrichment |
| `tests/test_merge.py` | Unit tests for merge logic |
| `tests/test_syllabus_utils.py` | Unit tests for compact syllabus builder |

---

## Pre-requisites (do these manually before starting)

1. Install Poppler (required by pdf2image on Windows):
   - Download from: https://github.com/oschwartz10612/poppler-windows/releases
   - Extract and add the `bin/` folder to your `PATH`

2. Set environment variable:
   ```
   $env:ANTHROPIC_API_KEY = "sk-ant-..."
   ```

3. Ensure Python 3.10+ is active.

---

## Task 1: Project scaffold

**Files:**
- Create: `requirements.txt`
- Create: `lib/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_schema_utils.py` (stub)

- [ ] **Step 1: Create `requirements.txt`**

```
anthropic>=0.40.0
pdf2image>=1.17.0
Pillow>=10.0.0
pytest>=8.0.0
```

- [ ] **Step 2: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 3: Create package markers**

`lib/__init__.py` — empty file.
`tests/__init__.py` — empty file.

- [ ] **Step 4: Create stub test file**

`tests/test_schema_utils.py`:
```python
# tests/test_schema_utils.py
```

- [ ] **Step 5: Verify pytest runs**

```bash
pytest tests/ -v
```

Expected: `no tests ran` (0 collected).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt lib/__init__.py tests/__init__.py tests/test_schema_utils.py
git commit -m "chore: scaffold project structure"
```

---

## Task 2: PDF → images utility

**Files:**
- Create: `lib/pdf_utils.py`

- [ ] **Step 1: Write `lib/pdf_utils.py`**

```python
# lib/pdf_utils.py
from pathlib import Path
from pdf2image import convert_from_path


def pdf_to_images(pdf_path: str, output_dir: str, prefix: str, dpi: int = 200) -> list[str]:
    """Convert each PDF page to a PNG. Returns list of image paths.
    Skips pages where the output file already exists (resume-safe).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    pages = convert_from_path(pdf_path, dpi=dpi)
    paths = []
    for i, page in enumerate(pages, 1):
        fname = f"{prefix}_p{i:03d}.png"
        fpath = out / fname
        if not fpath.exists():
            page.save(str(fpath), "PNG")
        paths.append(str(fpath))
    return paths
```

- [ ] **Step 2: Verify manually (no automated test — needs real PDF)**

```bash
python -c "
from lib.pdf_utils import pdf_to_images
paths = pdf_to_images('pdf/G1/TNPSC_Group1_2025_GeneralStudies.pdf', 'images/G1/2025/gs', 'TNPSC_G1_2025_gs')
print(f'Generated {len(paths)} images')
print(paths[:3])
"
```

Expected: prints count of pages and first 3 image paths.

- [ ] **Step 3: Commit**

```bash
git add lib/pdf_utils.py
git commit -m "feat: add PDF-to-images utility"
```

---

## Task 3: Claude Vision API client

**Files:**
- Create: `lib/api_client.py`

- [ ] **Step 1: Write `lib/api_client.py`**

```python
# lib/api_client.py
import base64
import json
import re
import time
from pathlib import Path

import anthropic

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192
MAX_RETRIES = 2
RETRY_DELAY = 5


def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def _strip_json(text: str) -> str:
    """Remove markdown code fences if present."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_questions_from_image(image_path: str, prompt: str) -> list[dict]:
    """Send one page image to Claude. Returns list of question dicts.
    Retries once on parse failure. Raises ValueError if both attempts fail.
    """
    client = anthropic.Anthropic()
    image_data = _encode_image(image_path)

    for attempt in range(1, MAX_RETRIES + 1):
        message = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = message.content[0].text
        try:
            parsed = json.loads(_strip_json(raw))
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "questions" in parsed:
                return parsed["questions"]
            raise ValueError(f"Unexpected JSON shape: {list(parsed.keys())}")
        except (json.JSONDecodeError, ValueError) as e:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                raise ValueError(f"JSON parse failed after {MAX_RETRIES} attempts: {e}\nRaw:\n{raw[:500]}")
```

- [ ] **Step 2: Smoke-test the client (requires API key and a real image)**

```bash
python -c "
from lib.api_client import extract_questions_from_image
# Use any already-extracted image from Task 2
qs = extract_questions_from_image('images/G1/2025/gs/TNPSC_G1_2025_gs_p001.png', 'Return a JSON array with one object: {\"test\": true}')
print(qs)
"
```

Expected: prints `[{'test': True}]` or similar.

- [ ] **Step 3: Commit**

```bash
git add lib/api_client.py
git commit -m "feat: add Claude Vision API client with retry"
```

---

## Task 4: Schema utilities — ID, enrichment, state

**Files:**
- Create: `lib/schema_utils.py`
- Modify: `tests/test_schema_utils.py`

- [ ] **Step 1: Write failing tests**

`tests/test_schema_utils.py`:
```python
# tests/test_schema_utils.py
import json
import tempfile
from pathlib import Path

import pytest

from lib.schema_utils import (
    generate_id,
    enrich_question,
    load_or_create_state,
    save_state,
    LANGUAGE_MODE,
)


def test_generate_id_basic():
    assert generate_id("G1", 2025, "gs", 1) == "tnpsc_g1_2025_gs_001"


def test_generate_id_subgroup():
    assert generate_id("G1", 2023, "gs", 42, subgroup="1A") == "tnpsc_g1_1a_2023_gs_042"


def test_generate_id_en_paper():
    assert generate_id("G2", 2024, "en", 100) == "tnpsc_g2_2024_en_100"


def test_enrich_question_adds_id():
    q = {"question_no": 5, "question_type": "simple_mcq"}
    enriched = enrich_question(q, group="G1", year=2025, paper_type="gs",
                               image_ref="images/G1/2025/gs/p001.png")
    assert enriched["id"] == "tnpsc_g1_2025_gs_005"
    assert enriched["meta"]["image_ref"] == "images/G1/2025/gs/p001.png"
    assert enriched["meta"]["extraction_status"] == "extracted"


def test_enrich_question_preserves_existing_meta():
    q = {"question_no": 1, "question_type": "simple_mcq",
         "meta": {"ocr_confidence": 0.9, "notes": "smudge"}}
    enriched = enrich_question(q, group="G2", year=2024, paper_type="en",
                               image_ref="images/p001.png")
    assert enriched["meta"]["ocr_confidence"] == 0.9
    assert enriched["meta"]["notes"] == "smudge"


def test_language_mode_bilingual():
    assert LANGUAGE_MODE["gs"] == "bilingual"
    assert LANGUAGE_MODE["en"] == "en_only"
    assert LANGUAGE_MODE["ta"] == "ta_only"


def test_state_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = Path(tmpdir) / "state.json"
        state = load_or_create_state(str(state_path), group="G1", year=2025, paper_type="gs", total_pages=10)
        assert state["processed_pages"] == []
        assert state["failed_pages"] == []
        state["processed_pages"].append(1)
        save_state(str(state_path), state)
        reloaded = load_or_create_state(str(state_path), group="G1", year=2025, paper_type="gs", total_pages=10)
        assert reloaded["processed_pages"] == [1]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_schema_utils.py -v
```

Expected: `ImportError` or `ModuleNotFoundError`.

- [ ] **Step 3: Write `lib/schema_utils.py`**

```python
# lib/schema_utils.py
import json
from datetime import datetime
from pathlib import Path

LANGUAGE_MODE: dict[str, str] = {
    "gs": "bilingual",
    "en": "en_only",
    "ta": "ta_only",
}

GROUP_ORDER = ["G1", "G2", "G3", "G4"]
PAPER_TYPE_ORDER = ["gs", "en", "ta"]


def generate_id(group: str, year: int, paper_type: str, question_no: int,
                subgroup: str | None = None) -> str:
    """Build globally unique question ID.
    Format: tnpsc_{group}[_{subgroup}]_{year}_{paper_type}_{N:03d}
    """
    parts = ["tnpsc", group.lower()]
    if subgroup:
        parts.append(subgroup.lower().replace(" ", "_"))
    parts += [str(year), paper_type, f"{question_no:03d}"]
    return "_".join(parts)


def enrich_question(q: dict, group: str, year: int, paper_type: str,
                    image_ref: str, subgroup: str | None = None) -> dict:
    """Add id, group, year, paper_type, language_mode, and meta fields."""
    q = dict(q)
    q["id"] = generate_id(group, year, paper_type, q["question_no"], subgroup)
    q["group"] = group
    q["year"] = year
    q["paper_type"] = paper_type
    q["language_mode"] = LANGUAGE_MODE.get(paper_type, "bilingual")

    existing_meta = q.get("meta") or {}
    q["meta"] = {
        "extraction_status": "extracted",
        "ocr_confidence": existing_meta.get("ocr_confidence"),
        "image_ref": image_ref,
        "notes": existing_meta.get("notes", ""),
    }
    return q


def load_or_create_state(state_path: str, group: str, year: int,
                          paper_type: str, total_pages: int) -> dict:
    p = Path(state_path)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {
        "group": group,
        "year": year,
        "paper_type": paper_type,
        "total_pages": total_pages,
        "processed_pages": [],
        "failed_pages": [],
        "last_updated": datetime.utcnow().isoformat(),
    }


def save_state(state_path: str, state: dict) -> None:
    state["last_updated"] = datetime.utcnow().isoformat()
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_schema_utils.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/schema_utils.py tests/test_schema_utils.py
git commit -m "feat: add schema utilities — ID generation, enrichment, state I/O"
```

---

## Task 5: Syllabus utilities

**Files:**
- Create: `lib/syllabus_utils.py`
- Create: `tests/test_syllabus_utils.py`

- [ ] **Step 1: Write failing tests**

`tests/test_syllabus_utils.py`:
```python
# tests/test_syllabus_utils.py
from lib.syllabus_utils import build_compact_syllabus, load_syllabus


def test_load_syllabus_returns_units():
    syllabus = load_syllabus("tnpsc_general_studies_aptitude_mental_ability_syllabus.json")
    assert len(syllabus["units"]) == 8
    assert syllabus["units"][0]["unit"] == 1


def test_compact_syllabus_contains_all_units():
    syllabus = load_syllabus("tnpsc_general_studies_aptitude_mental_ability_syllabus.json")
    compact = build_compact_syllabus(syllabus)
    assert "Unit 1: General Science" in compact
    assert "Unit 8: Reasoning" in compact


def test_compact_syllabus_contains_themes():
    syllabus = load_syllabus("tnpsc_general_studies_aptitude_mental_ability_syllabus.json")
    compact = build_compact_syllabus(syllabus)
    assert "Physics" in compact
    assert "Tamil Literature" in compact


def test_compact_syllabus_contains_topics():
    syllabus = load_syllabus("tnpsc_general_studies_aptitude_mental_ability_syllabus.json")
    compact = build_compact_syllabus(syllabus)
    assert "Sangam Literature" in compact
    assert "Ratio and Proportion" in compact
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_syllabus_utils.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write `lib/syllabus_utils.py`**

```python
# lib/syllabus_utils.py
import json
from pathlib import Path


def load_syllabus(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_compact_syllabus(syllabus: dict) -> str:
    """Build a compact text reference for the extraction prompt.
    Format per unit:
        Unit N: Name
          Theme: ThemeName → Topics: topic1, topic2, ...
    """
    lines = []
    for unit in syllabus["units"]:
        lines.append(f"Unit {unit['unit']}: {unit['name']}")
        for theme in unit["themes"]:
            topic_names = [t["topic_name"] for t in theme["topics"]]
            lines.append(f"  Theme: {theme['theme']} → Topics: {', '.join(topic_names)}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_syllabus_utils.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/syllabus_utils.py tests/test_syllabus_utils.py
git commit -m "feat: add syllabus utilities for compact tagging reference"
```

---

## Task 6: Extraction prompts

**Files:**
- Create: `prompt_extract.txt`
- Create: `prompt_reextract.txt`

- [ ] **Step 1: Write `prompt_extract.txt`**

```
You are extracting questions from a TNPSC (Tamil Nadu Public Service Commission) exam question paper image.

PAPER INFO: {PAPER_INFO}
LANGUAGE MODE: {LANGUAGE_MODE}

LANGUAGE MODE RULES:
- bilingual: Each question has English text followed immediately by Tamil translation. Extract BOTH into "en" and "ta" blocks.
- en_only: All questions are in English only. Populate "en" block. Set "ta" to null.
- ta_only: All questions are in Tamil only. Populate "ta" block. Set "en" to null.

QUESTION TYPES (classify each question as exactly one):
1. simple_mcq — single stem + options
2. statement_mcq — stem + numbered statements (roman/arabic/alpha) + options
3. match_the_following — stem + two columns or inline pairs + options
4. assertion_reason — Assertion [A] block + Reason [R] block + options
5. chronological_order — stem + items to order + options
6. other — does not fit any of the above; use raw_content (see below)

OUTPUT FORMAT:
Return a JSON array. Each element is one question object.
No markdown, no code fences, no explanation — raw JSON only.

QUESTION OBJECT SCHEMA:
{
  "question_no": <integer>,
  "question_type": "<one of the 6 types>",
  "unit_id": <integer 1-8>,
  "unit": "<exact unit name from syllabus>",
  "theme": "<exact theme name from syllabus>",
  "topic": "<exact topic_name from syllabus>",
  "disputed": <boolean — true if red expert-committee annotation visible>,
  "stem_has_blank": <boolean — true if stem has a fill-in-blank dash or underline>,
  "has_math": <boolean — true if stem or any option contains a formula or equation>,
  "math_format": <"plain_text" | "latex" | "unicode" | null>,
  "options_layout": <"single_column" | "two_column">,
  "media": <null | array of media blocks>,
  "en": <language block or null>,
  "ta": <language block or null>,
  "answer_key": <"A"|"B"|"C"|"D"|"E"|"X"|null>,
  "meta": {"extraction_status": "extracted", "ocr_confidence": <0.0-1.0 or null>, "notes": ""}
}

LANGUAGE BLOCK — simple_mcq:
{
  "stem": "<question text>",
  "question_keywords": ["<exact term from text>", ...],
  "options": [{"key": "A", "text": "..."}, ...]
}

LANGUAGE BLOCK — statement_mcq:
{
  "stem": "<question text>",
  "question_keywords": ["<exact term>", ...],
  "item_numbering_style": "roman|arabic|alpha",
  "statements": [{"no": "i", "text": "..."}, ...],
  "options": [{"key": "A", "text": "..."}, ...]
}

LANGUAGE BLOCK — match_the_following (tabular — left a/b/c/d, right 1/2/3/4/5, matrix options):
{
  "stem": "<question text>",
  "question_keywords": ["<exact term>", ...],
  "match_style": "tabular",
  "left_column": [{"key": "a", "text": "..."}, ...],
  "right_column": [{"no": 1, "text": "..."}, ...],
  "options": [
    {"key": "A", "mapping": {"a": 5, "b": 4, "c": 1, "d": 3}},
    {"key": "B", "mapping": {"a": 2, "b": 4, "c": 3, "d": 1}},
    {"key": "C", "mapping": {"a": 5, "b": 4, "c": 1, "d": 3}},
    {"key": "D", "mapping": {"a": 2, "b": 4, "c": 1, "d": 3}},
    {"key": "E", "text": "Answer not known"}
  ]
}

LANGUAGE BLOCK — match_the_following (inline — each item contains its own pair):
{
  "stem": "<question text>",
  "question_keywords": ["<exact term>", ...],
  "match_style": "inline",
  "items": [{"no": "1", "text": "<full pair text>"}, ...],
  "options": [{"key": "A", "text": "..."}, ...]
}

LANGUAGE BLOCK — assertion_reason:
{
  "assertion": "<assertion text>",
  "reason": "<reason text>",
  "question_keywords": ["<exact term>", ...],
  "options": [{"key": "A", "text": "<verbatim option text>"}, ...]
}

LANGUAGE BLOCK — chronological_order:
{
  "stem": "<question text>",
  "question_keywords": ["<exact term>", ...],
  "item_numbering_style": "roman|arabic",
  "items": [{"no": "1", "text": "..."}, ...],
  "options": [{"key": "A", "text": "..."}, ...]
}

LANGUAGE BLOCK — other (catch-all):
{
  "raw_content": "<complete verbatim text of entire question, every word>"
}

MEDIA BLOCK (when image/diagram present in question):
{"id": "img_qN_desc", "type": "image|diagram|formula|table", "placement": "shared_stem|en_stem|ta_stem|en_options|ta_options", "path": "media/qN_desc.png", "alt_text": "<description>"}

ANSWER KEY DETECTION:
- Find the option with a tick (✓), circle (●○), or any visible mark.
- Set answer_key to that option key letter (A/B/C/D/E).
- If the question is marked omitted, deleted, under review, or referred to expert committee: set answer_key to "X".
- If no mark is visible or legible: set answer_key to null.

QUESTION KEYWORDS RULE:
- Extract 2–6 key terms exactly as they appear in the text (same language as the block).
- Include: proper nouns, technical terms, scheme names, article/section numbers, named acts, named persons, named events.
- Do NOT paraphrase, generalise, or translate.

SYLLABUS TAGGING:
Use the following syllabus to assign unit_id, unit, theme, topic.
If no exact topic match, use the nearest unit and set topic to "Miscellaneous".
Use exact unit names and theme names from this list only:

{SYLLABUS_COMPACT}

COMPLETENESS RULE — NON-NEGOTIABLE:
- Capture EVERY word, number, symbol, punctuation mark, and letter exactly as printed.
- Do NOT summarise, shorten, paraphrase, or rephrase any text.
- Do NOT skip any part of the stem, statements, items, or options.
- If a word or character is unclear or smudged, write [UNCLEAR] at that position. Do NOT omit it.
- If a question does not match any of the 5 known types, classify it as "other" and capture all its text verbatim in raw_content.
- Missing even one word is an extraction failure.

Return ONLY the JSON array. No other text.
```

- [ ] **Step 2: Write `prompt_reextract.txt`**

```
You are analysing a TNPSC exam question image that contains a question which was classified as "other" (unrecognised structure) in a previous extraction pass.

Your task:
1. Analyse the structure of the question carefully.
2. Propose a new question type name in snake_case (e.g., "fill_in_blank_pair", "reading_comprehension", "series_completion").
3. Define a JSON language-block schema for this type, following the same style as existing types (each field is a string, array, or object — no nesting beyond two levels).
4. Extract the question fully using your proposed schema.

Return ONLY this JSON object — no other text:
{
  "proposed_type": "<snake_case name>",
  "schema": {
    "<field_name>": "<type description e.g. 'string — question text'>",
    "question_keywords": "array of string — exact key terms",
    "options": "array of {key: A|B|C|D|E, text: string}"
  },
  "extracted": {
    "en": { <language block using your schema> },
    "ta": { <language block using your schema> },
    "answer_key": "<A|B|C|D|E|X|null>"
  }
}

COMPLETENESS RULE: Capture every word exactly as printed. Do not summarise or omit any text.
```

- [ ] **Step 3: Verify files exist**

```bash
python -c "
with open('prompt_extract.txt') as f: print('extract prompt lines:', len(f.readlines()))
with open('prompt_reextract.txt') as f: print('reextract prompt lines:', len(f.readlines()))
"
```

Expected: both files readable, each > 10 lines.

- [ ] **Step 4: Commit**

```bash
git add prompt_extract.txt prompt_reextract.txt
git commit -m "feat: add extraction prompts for Pass 1 and Pass 2"
```

---

## Task 7: `extract.py`

**Files:**
- Create: `extract.py`

- [ ] **Step 1: Write `extract.py`**

```python
# extract.py
"""
Usage:
  python extract.py pdf/G1/TNPSC_Group1_2025_GeneralStudies.pdf --group G1 --year 2025 --paper-type gs
  python extract.py ... --subgroup 1A
  python extract.py ... --retry-failed
"""
import argparse
import json
import sys
from pathlib import Path

from lib.api_client import extract_questions_from_image
from lib.pdf_utils import pdf_to_images
from lib.schema_utils import (
    LANGUAGE_MODE,
    enrich_question,
    load_or_create_state,
    save_state,
)
from lib.syllabus_utils import build_compact_syllabus, load_syllabus


def build_data_key(group: str, year: int, paper_type: str, subgroup: str | None) -> str:
    parts = [group, str(year), paper_type]
    if subgroup:
        parts.insert(1, subgroup.replace(" ", "_"))
    return "_".join(parts)


def build_image_prefix(group: str, year: int, paper_type: str, subgroup: str | None) -> str:
    parts = ["TNPSC", group, str(year), paper_type]
    if subgroup:
        parts.insert(2, subgroup)
    return "_".join(parts)


def build_prompt(template: str, language_mode: str, paper_info: str, syllabus_compact: str) -> str:
    return (template
            .replace("{LANGUAGE_MODE}", language_mode)
            .replace("{PAPER_INFO}", paper_info)
            .replace("{SYLLABUS_COMPACT}", syllabus_compact))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path")
    parser.add_argument("--group", required=True)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--paper-type", required=True, choices=["gs", "en", "ta"])
    parser.add_argument("--subgroup", default=None)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--syllabus", default="tnpsc_general_studies_aptitude_mental_ability_syllabus.json")
    args = parser.parse_args()

    data_key = build_data_key(args.group, args.year, args.paper_type, args.subgroup)
    image_dir = f"images/{args.group}/{args.year}/{args.paper_type}"
    image_prefix = build_image_prefix(args.group, args.year, args.paper_type, args.subgroup)
    state_path = f"state/{data_key}_progress.json"
    partial_path = f"data/{data_key}_partial.json"
    final_path = f"data/{data_key}.json"

    Path("data").mkdir(exist_ok=True)
    Path("state").mkdir(exist_ok=True)

    # Load prompt
    with open("prompt_extract.txt", encoding="utf-8") as f:
        prompt_template = f.read()

    # Build syllabus compact
    syllabus = load_syllabus(args.syllabus)
    syllabus_compact = build_compact_syllabus(syllabus)
    language_mode = LANGUAGE_MODE[args.paper_type]
    paper_info = f"Group={args.group}, Year={args.year}, PaperType={args.paper_type}, SubGroup={args.subgroup}"
    prompt = build_prompt(prompt_template, language_mode, paper_info, syllabus_compact)

    # Convert PDF to images
    print(f"Converting PDF: {args.pdf_path}")
    image_paths = pdf_to_images(args.pdf_path, image_dir, image_prefix)
    total_pages = len(image_paths)
    print(f"  {total_pages} pages")

    # Load state
    state = load_or_create_state(state_path, args.group, args.year, args.paper_type, total_pages)

    # Determine which pages to process
    if args.retry_failed:
        pages_to_process = list(state["failed_pages"])
        state["failed_pages"] = []
        print(f"Retrying {len(pages_to_process)} failed pages")
    else:
        done = set(state["processed_pages"])
        pages_to_process = [i for i in range(1, total_pages + 1) if i not in done]
        print(f"Processing {len(pages_to_process)} of {total_pages} pages")

    # Load existing partial data
    all_questions: list[dict] = []
    if Path(partial_path).exists():
        with open(partial_path, encoding="utf-8") as f:
            data = json.load(f)
            all_questions = data.get("questions", [])

    # Process each page
    extracted_count = 0
    for page_no in pages_to_process:
        image_path = image_paths[page_no - 1]
        print(f"  Page {page_no}/{total_pages}: {Path(image_path).name}", end=" ", flush=True)

        try:
            questions = extract_questions_from_image(image_path, prompt)
        except ValueError as e:
            print(f"FAILED — {e}")
            if page_no not in state["failed_pages"]:
                state["failed_pages"].append(page_no)
            save_state(state_path, state)
            continue

        for q in questions:
            enriched = enrich_question(
                q,
                group=args.group,
                year=args.year,
                paper_type=args.paper_type,
                image_ref=image_path,
                subgroup=args.subgroup,
            )
            all_questions.append(enriched)

        extracted_count += len(questions)
        print(f"→ {len(questions)} questions")

        if page_no not in state["processed_pages"]:
            state["processed_pages"].append(page_no)
        if page_no in state["failed_pages"]:
            state["failed_pages"].remove(page_no)

        # Write partial immediately (resume safety)
        wrapper = {
            "group": args.group, "subgroup": args.subgroup, "year": args.year,
            "paper_type": args.paper_type,
            "language_mode": language_mode,
            "paper_code": "",
            "total_questions": len(all_questions),
            "questions": all_questions,
        }
        with open(partial_path, "w", encoding="utf-8") as f:
            json.dump(wrapper, f, indent=2, ensure_ascii=False)

        save_state(state_path, state)

    # Validate and finalise
    all_nums = sorted(set(q["question_no"] for q in all_questions))
    if all_nums:
        expected = list(range(all_nums[0], all_nums[-1] + 1))
        missing = [n for n in expected if n not in all_nums]
        if missing:
            print(f"\nWARNING: Missing question numbers: {missing}")

    if not state["failed_pages"]:
        Path(partial_path).rename(final_path)
        print(f"\nDone. {extracted_count} questions extracted → {final_path}")
    else:
        print(f"\nPartial complete. Failed pages: {state['failed_pages']}. Run with --retry-failed.")

    print(f"Processed: {len(state['processed_pages'])}/{total_pages} pages | Failed: {len(state['failed_pages'])}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run help to verify argument parsing**

```bash
python extract.py --help
```

Expected: usage message with all arguments listed.

- [ ] **Step 3: Run on one real PDF to verify end-to-end**

```bash
python extract.py pdf/G1/TNPSC_Group1_2025_GeneralStudies.pdf --group G1 --year 2025 --paper-type gs
```

Expected: pages convert, Claude API called per page, `data/G1_2025_gs.json` created (or `_partial.json` if not all pages done).

- [ ] **Step 4: Commit**

```bash
git add extract.py
git commit -m "feat: add extract.py — PDF to JSON with resume support"
```

---

## Task 8: `reextract.py`

**Files:**
- Create: `reextract.py`

- [ ] **Step 1: Write `reextract.py`**

```python
# reextract.py
"""
Usage:
  # Propose types for all "other" questions in a JSON file
  python reextract.py --json data/G1_2025_gs.json --type other

  # Re-extract a specific page image and patch matching question records
  python reextract.py --image images/G1/2025/gs/TNPSC_G1_2025_gs_p003.png --json data/G1_2025_gs.json
"""
import argparse
import json
from pathlib import Path

from lib.api_client import extract_questions_from_image


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def propose_types(json_path: str) -> None:
    """For each 'other' question, send its image back to Claude and print proposal."""
    with open("prompt_reextract.txt", encoding="utf-8") as f:
        prompt = f.read()

    data = load_json(json_path)
    others = [q for q in data["questions"] if q.get("question_type") == "other"]

    if not others:
        print("No 'other' type questions found.")
        return

    # Group by image_ref to minimise API calls
    by_image: dict[str, list[dict]] = {}
    for q in others:
        img = q.get("meta", {}).get("image_ref", "")
        by_image.setdefault(img, []).append(q)

    print(f"Found {len(others)} 'other' questions across {len(by_image)} images.\n")

    for image_ref, qs in by_image.items():
        print(f"--- Image: {image_ref} (Q{[q['question_no'] for q in qs]}) ---")
        if not Path(image_ref).exists():
            print(f"  Image not found: {image_ref}")
            continue
        try:
            result = extract_questions_from_image(image_ref, prompt)
            if isinstance(result, list):
                result = result[0]
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"  ERROR: {e}")
        print()


def reextract_image(image_path: str, json_path: str) -> None:
    """Re-extract all questions from one image and patch matching records in JSON."""
    with open("prompt_extract.txt", encoding="utf-8") as f:
        prompt_template = f.read()

    data = load_json(json_path)
    language_mode = data.get("language_mode", "bilingual")
    paper_info = f"Group={data.get('group')}, Year={data.get('year')}, PaperType={data.get('paper_type')}"

    from lib.syllabus_utils import build_compact_syllabus, load_syllabus
    syllabus = load_syllabus("tnpsc_general_studies_aptitude_mental_ability_syllabus.json")
    syllabus_compact = build_compact_syllabus(syllabus)

    prompt = (prompt_template
              .replace("{LANGUAGE_MODE}", language_mode)
              .replace("{PAPER_INFO}", paper_info)
              .replace("{SYLLABUS_COMPACT}", syllabus_compact))

    print(f"Re-extracting: {image_path}")
    new_qs = extract_questions_from_image(image_path, prompt)
    print(f"  Got {len(new_qs)} questions from API")

    # Build index of new questions by question_no
    new_by_no = {q["question_no"]: q for q in new_qs}

    patched = 0
    for i, q in enumerate(data["questions"]):
        if q.get("meta", {}).get("image_ref") == image_path:
            if q["question_no"] in new_by_no:
                # Preserve id and meta.image_ref from original
                new_q = dict(new_by_no[q["question_no"]])
                new_q["id"] = q["id"]
                new_q.setdefault("meta", {})["image_ref"] = image_path
                new_q["meta"]["extraction_status"] = "extracted"
                data["questions"][i] = new_q
                patched += 1

    save_json(json_path, data)
    print(f"  Patched {patched} records in {json_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True, help="Path to per-PDF data JSON")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--type", choices=["other"], help="Propose types for questions of this type")
    group.add_argument("--image", help="Re-extract this specific page image")
    args = parser.parse_args()

    if args.type == "other":
        propose_types(args.json)
    elif args.image:
        reextract_image(args.image, args.json)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify help**

```bash
python reextract.py --help
```

Expected: usage message listing `--json`, `--type`, `--image`.

- [ ] **Step 3: Commit**

```bash
git add reextract.py
git commit -m "feat: add reextract.py — targeted re-extraction for other-type questions"
```

---

## Task 9: `merge.py`

**Files:**
- Create: `merge.py`
- Create: `tests/test_merge.py`

- [ ] **Step 1: Write failing tests**

`tests/test_merge.py`:
```python
# tests/test_merge.py
import json
import tempfile
from pathlib import Path

import pytest

from merge import build_sort_key, load_data_files, merge_to_master


def _write_json(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f)


def test_build_sort_key_ordering():
    q1 = {"group": "G1", "year": 2025, "paper_type": "gs", "question_no": 1}
    q2 = {"group": "G2", "year": 2025, "paper_type": "gs", "question_no": 1}
    q3 = {"group": "G1", "year": 2024, "paper_type": "gs", "question_no": 1}
    assert build_sort_key(q1) < build_sort_key(q2)
    assert build_sort_key(q3) < build_sort_key(q1)


def test_merge_deduplicates_ids():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        q = {"id": "tnpsc_g1_2025_gs_001", "group": "G1", "year": 2025,
             "paper_type": "gs", "question_no": 1}
        _write_json(data_dir / "G1_2025_gs.json", {"questions": [q]})
        _write_json(data_dir / "G1_2025_gs_copy.json", {"questions": [q]})
        with pytest.raises(ValueError, match="Duplicate"):
            merge_to_master(str(data_dir), "/dev/null")


def test_merge_output_sorted():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        out_path = str(data_dir / "master.json")
        q1 = {"id": "tnpsc_g2_2025_gs_001", "group": "G2", "year": 2025,
              "paper_type": "gs", "question_no": 1}
        q2 = {"id": "tnpsc_g1_2025_gs_001", "group": "G1", "year": 2025,
              "paper_type": "gs", "question_no": 1}
        _write_json(data_dir / "G2_2025_gs.json", {"questions": [q1]})
        _write_json(data_dir / "G1_2025_gs.json", {"questions": [q2]})
        merge_to_master(str(data_dir), out_path)
        with open(out_path) as f:
            master = json.load(f)
        assert master["questions"][0]["group"] == "G1"
        assert master["total_questions"] == 2
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_merge.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write `merge.py`**

```python
# merge.py
import json
from pathlib import Path

from lib.schema_utils import GROUP_ORDER, PAPER_TYPE_ORDER


def build_sort_key(q: dict) -> tuple:
    group_rank = GROUP_ORDER.index(q["group"]) if q["group"] in GROUP_ORDER else 99
    paper_rank = PAPER_TYPE_ORDER.index(q["paper_type"]) if q["paper_type"] in PAPER_TYPE_ORDER else 99
    return (group_rank, q["year"], paper_rank, q["question_no"])


def load_data_files(data_dir: str) -> list[dict]:
    questions = []
    seen_ids: set[str] = set()
    for path in sorted(Path(data_dir).glob("*.json")):
        if "_partial" in path.name:
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for q in data.get("questions", []):
            qid = q.get("id", "")
            if qid in seen_ids:
                raise ValueError(f"Duplicate ID: {qid} (found in {path.name})")
            seen_ids.add(qid)
            questions.append(q)
    return questions


def merge_to_master(data_dir: str, output_path: str) -> None:
    questions = load_data_files(data_dir)
    questions.sort(key=build_sort_key)
    master = {
        "total_questions": len(questions),
        "questions": questions,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(master, f, indent=2, ensure_ascii=False)
    print(f"master.json: {len(questions)} questions → {output_path}")


if __name__ == "__main__":
    merge_to_master("data", "master.json")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_merge.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add merge.py tests/test_merge.py
git commit -m "feat: add merge.py — per-PDF JSONs to master.json"
```

---

## Task 10: HTML template — shell, filters, language toggle, browse mode

**Files:**
- Create: `lib/template.html`

This task builds the complete HTML structure, all 6 filter cards, the language toggle, and `simple_mcq` rendering. The remaining 5 question type renderers are added in Task 11.

- [ ] **Step 1: Create `lib/template.html`**

Full file — create `lib/template.html` with the content below.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TNPSC PYQs</title>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js" async></script>
<style>
:root {
  --accent: #e05a00;
  --accent-light: #fff3ed;
  --correct: #16a34a;
  --wrong: #dc2626;
  --disputed: #dc2626;
  --unclassified: #ca8a04;
  --card-bg: #fff;
  --border: #e5e7eb;
  --muted: #6b7280;
  --body-bg: #f9fafb;
  font-family: system-ui, sans-serif;
}
body { background: var(--body-bg); margin: 0; padding: 0; }
header {
  background: #fff; border-bottom: 1px solid var(--border);
  padding: 0.75rem 1.25rem; display: flex; align-items: center;
  gap: 1rem; position: sticky; top: 0; z-index: 100;
}
header h1 { margin: 0; font-size: 1.1rem; color: var(--accent); flex: 1; }
.lang-toggle { display: flex; gap: 0.25rem; }
.lang-btn {
  border: 1px solid var(--border); background: #fff; cursor: pointer;
  padding: 0.3rem 0.7rem; border-radius: 4px; font-size: 0.82rem;
}
.lang-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
main { display: flex; gap: 1rem; padding: 1rem 1.25rem; max-width: 1400px; margin: 0 auto; }
.sidebar { width: 280px; min-width: 240px; flex-shrink: 0; display: flex; flex-direction: column; gap: 0.75rem; }
.browse-area { flex: 1; min-width: 0; }
.filter-card {
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
  padding: 0.75rem; display: flex; flex-direction: column; gap: 0.5rem;
}
.filter-card h3 { margin: 0; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
.toggle-btn {
  border: 1px solid var(--border); background: #fff; cursor: pointer;
  padding: 0.25rem 0.6rem; border-radius: 4px; font-size: 0.82rem;
  transition: background 0.1s;
}
.toggle-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.toggle-group { display: flex; flex-wrap: wrap; gap: 0.3rem; }
.single-select .toggle-btn.active { background: var(--accent); color: #fff; }
.subject-select { width: 100%; padding: 0.35rem 0.5rem; border: 1px solid var(--border); border-radius: 4px; font-size: 0.85rem; }
input[type=text] {
  width: 100%; box-sizing: border-box; padding: 0.35rem 0.5rem;
  border: 1px solid var(--border); border-radius: 4px; font-size: 0.85rem;
}
.quiz-count { font-size: 2rem; font-weight: 700; color: var(--accent); font-variant-numeric: tabular-nums; }
.quiz-label { font-size: 0.78rem; color: var(--muted); }
.start-btn {
  background: var(--accent); color: #fff; border: none; border-radius: 6px;
  padding: 0.5rem 1rem; font-size: 0.9rem; cursor: pointer; width: 100%;
}
.start-btn:disabled { background: #d1d5db; cursor: not-allowed; }
.result-count { font-size: 0.82rem; color: var(--muted); margin-bottom: 0.5rem; }
.questions-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 0.75rem; }
.question-card {
  background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 1rem; border-left: 3px solid transparent; transition: border-left-color 0.15s;
}
.question-card:hover { border-left-color: var(--accent); }
.q-meta { display: flex; gap: 0.4rem; align-items: center; margin-bottom: 0.5rem; flex-wrap: wrap; }
.q-num { font-family: monospace; font-size: 0.72rem; font-weight: 600; background: var(--accent-light); color: var(--accent); padding: 0.1rem 0.4rem; border-radius: 3px; }
.q-year { font-family: monospace; font-size: 0.68rem; color: var(--muted); }
.badge-disputed { background: #fee2e2; color: var(--disputed); font-size: 0.65rem; font-weight: 700; padding: 0.1rem 0.4rem; border-radius: 3px; }
.badge-other { background: #fef9c3; color: var(--unclassified); font-size: 0.65rem; font-weight: 700; padding: 0.1rem 0.4rem; border-radius: 3px; }
.lang-block { margin-bottom: 0.5rem; }
.lang-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-bottom: 0.25rem; }
.q-stem { font-size: 0.88rem; margin: 0 0 0.4rem 0; line-height: 1.5; }
.statements { padding-left: 0; list-style: none; margin: 0 0 0.4rem 0; }
.statements li { font-size: 0.85rem; padding: 0.2rem 0 0.2rem 0.75rem; border-left: 2px solid var(--border); margin-bottom: 0.2rem; line-height: 1.5; }
.q-tail { font-size: 0.85rem; font-style: italic; border-left: 2px solid var(--accent-light); padding-left: 0.5rem; margin: 0.3rem 0 0.5rem; color: #374151; }
.ar-block { background: #f9fafb; border-radius: 4px; padding: 0.5rem 0.7rem; margin-bottom: 0.4rem; }
.ar-label { font-size: 0.7rem; font-weight: 700; color: var(--muted); margin-bottom: 0.2rem; }
.ar-text { font-size: 0.85rem; }
.match-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-bottom: 0.4rem; }
.match-table td { padding: 0.2rem 0.5rem; border: 1px solid var(--border); vertical-align: top; }
.match-items { list-style: none; padding-left: 0; margin: 0 0 0.4rem 0; }
.match-items li { font-size: 0.82rem; margin-bottom: 0.2rem; line-height: 1.4; padding-left: 0.75rem; border-left: 2px solid var(--border); }
.raw-content { font-family: monospace; font-size: 0.78rem; background: #f9fafb; padding: 0.5rem; border-radius: 4px; white-space: pre-wrap; word-break: break-word; }
.options-grid { display: grid; gap: 0.3rem; }
.options-2col { grid-template-columns: 1fr 1fr; }
.options-1col { grid-template-columns: 1fr; }
.option {
  display: flex; align-items: flex-start; gap: 0.4rem;
  border: 1px solid var(--border); border-radius: 5px;
  padding: 0.3rem 0.5rem; cursor: pointer; transition: background 0.1s;
}
.option:hover { background: var(--accent-light); }
.option.selected { border-color: var(--accent); background: var(--accent-light); }
.option.correct { border-color: var(--correct); background: #f0fdf4; }
.option.wrong { border-color: var(--wrong); background: #fef2f2; }
.opt-key {
  font-family: monospace; font-size: 0.72rem; font-weight: 700;
  min-width: 1.2rem; height: 1.2rem; display: flex; align-items: center;
  justify-content: center; border-radius: 3px; background: #f3f4f6; flex-shrink: 0;
}
.option.correct .opt-key { background: var(--correct); color: #fff; }
.option.wrong .opt-key { background: var(--wrong); color: #fff; }
.opt-text { font-size: 0.83rem; line-height: 1.45; }
.answer-row { font-size: 0.78rem; margin-top: 0.4rem; color: var(--muted); }
.answer-row .correct-ans { color: var(--correct); font-weight: 600; }
.answer-row .omitted { color: var(--muted); }
body.hide-answers .option.correct { border-color: var(--border) !important; background: #fff !important; }
body.hide-answers .option.correct .opt-key { background: #f3f4f6 !important; color: inherit !important; }
body.hide-answers .answer-row { display: none; }
/* Quiz */
#quiz-area { display: none; }
.quiz-header {
  display: flex; align-items: center; gap: 1rem;
  background: #fff; border-bottom: 1px solid var(--border);
  padding: 0.75rem 1.25rem; position: sticky; top: 56px; z-index: 90;
}
.quiz-score { font-family: monospace; font-size: 0.9rem; color: var(--accent); }
.quiz-nav-btn { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 0.4rem 1rem; cursor: pointer; }
.score-banner { background: var(--accent-light); border-radius: 8px; padding: 1rem 1.5rem; margin-bottom: 1rem; font-size: 1.1rem; font-weight: 600; color: var(--accent); text-align: center; }
</style>
</head>
<body>

<header>
  <h1>TNPSC PYQs</h1>
  <div class="lang-toggle">
    <button class="lang-btn active" onclick="setLang('en')">English</button>
    <button class="lang-btn" onclick="setLang('ta')">Tamil</button>
    <button class="lang-btn" onclick="setLang('both')">Both</button>
  </div>
</header>

<div id="browse-container">
<main>
  <aside class="sidebar">

    <!-- Group filter -->
    <div class="filter-card">
      <h3>Group</h3>
      <div class="toggle-group" id="group-btns"></div>
    </div>

    <!-- Paper Type filter (single-select) -->
    <div class="filter-card">
      <h3>Paper Type</h3>
      <div class="toggle-group single-select" id="papertype-btns">
        <button class="toggle-btn active" data-pt="gs" onclick="setPaperType('gs')">General Studies</button>
        <button class="toggle-btn" data-pt="en" onclick="setPaperType('en')">General English</button>
        <button class="toggle-btn" data-pt="ta" onclick="setPaperType('ta')">General Tamil</button>
      </div>
    </div>

    <!-- Year filter -->
    <div class="filter-card">
      <h3>Year</h3>
      <div class="toggle-group" id="year-btns"></div>
    </div>

    <!-- Subject (Unit) filter — single-select -->
    <div class="filter-card">
      <h3>Subject</h3>
      <select class="subject-select" id="unit-select" onchange="setUnit(this.value)">
        <option value="">All Subjects</option>
      </select>
    </div>

    <!-- Keyword -->
    <div class="filter-card">
      <h3>Keyword</h3>
      <input type="text" id="keyword-input" placeholder="Search keywords…" oninput="renderBrowse()">
    </div>

    <!-- Quiz card -->
    <div class="filter-card">
      <h3>Quiz</h3>
      <div class="quiz-count" id="quiz-count">0</div>
      <div class="quiz-label">questions</div>
      <button class="start-btn" id="start-quiz-btn" onclick="startQuiz()" disabled>Start Quiz</button>
    </div>

  </aside>

  <div class="browse-area">
    <div class="result-count" id="result-count"></div>
    <div class="questions-grid" id="questions-grid"></div>
  </div>
</main>
</div>

<!-- Quiz area -->
<div id="quiz-area">
  <div class="quiz-header">
    <div class="quiz-score" id="quiz-score-label"></div>
    <div style="flex:1"></div>
    <button class="quiz-nav-btn" id="quiz-next-btn" onclick="quizNext()" style="display:none">Next →</button>
    <button class="quiz-nav-btn" id="quiz-submit-btn" onclick="submitExam()" style="display:none">Submit</button>
    <button class="quiz-nav-btn" style="background:#6b7280" onclick="exitQuiz()">Exit</button>
  </div>
  <div style="padding:1rem 1.25rem; max-width:900px; margin:0 auto">
    <div id="quiz-content"></div>
  </div>
</div>

<script>
const QUESTIONS = __QUESTIONS_DATA__;
const SYLLABUS  = __SYLLABUS_DATA__;

// --- State ---
let _lang    = 'en';
let _groups  = new Set();
let _pt      = 'gs';
let _years   = new Set();
let _unit    = null;
let _keyword = '';

// Quiz state
let _qMode  = null;  // 'one' | 'exam'
let _qs     = [];
let _idx    = 0;
let _score  = 0;
let _ans    = {};    // question index → chosen key (exam mode)

// --- Init ---
function init() {
  const groups = [...new Set(QUESTIONS.map(q => q.group))].sort();
  const years  = [...new Set(QUESTIONS.map(q => q.year))].sort((a,b) => b - a);

  // Group buttons (multi-select, default G1)
  const gb = document.getElementById('group-btns');
  groups.forEach(g => {
    const btn = document.createElement('button');
    btn.className = 'toggle-btn' + (g === 'G1' ? ' active' : '');
    btn.textContent = g;
    btn.onclick = () => { _groups.has(g) ? _groups.delete(g) : _groups.add(g); btn.classList.toggle('active'); renderBrowse(); };
    gb.appendChild(btn);
    if (g === 'G1') _groups.add(g);
  });

  // Year buttons (multi-select, default latest)
  const yb = document.getElementById('year-btns');
  const latestYear = years[0];
  years.forEach(y => {
    const btn = document.createElement('button');
    btn.className = 'toggle-btn' + (y === latestYear ? ' active' : '');
    btn.textContent = y;
    btn.onclick = () => { _years.has(y) ? _years.delete(y) : _years.add(y); btn.classList.toggle('active'); renderBrowse(); };
    yb.appendChild(btn);
    if (y === latestYear) _years.add(y);
  });

  // Unit select
  const us = document.getElementById('unit-select');
  SYLLABUS.units.forEach(u => {
    const opt = document.createElement('option');
    opt.value = u.unit;
    opt.textContent = `${u.unit}. ${u.name}`;
    us.appendChild(opt);
  });

  renderBrowse();
}

// --- Filters ---
function setLang(l) {
  _lang = l;
  document.querySelectorAll('.lang-btn').forEach((b, i) => b.classList.toggle('active', ['en','ta','both'][i] === l));
  renderBrowse();
}

function setPaperType(pt) {
  _pt = pt;
  document.querySelectorAll('#papertype-btns .toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.pt === pt));
  renderBrowse();
}

function setUnit(val) {
  _unit = val ? parseInt(val) : null;
  renderBrowse();
}

function getFiltered() {
  return QUESTIONS.filter(q => {
    if (!_groups.has(q.group)) return false;
    if (q.paper_type !== _pt) return false;
    if (!_years.has(q.year)) return false;
    if (_unit !== null && q.unit_id !== _unit) return false;
    if (_keyword) {
      const kws = _lang === 'ta'
        ? (q.ta?.question_keywords || [])
        : (q.en?.question_keywords || []);
      if (!kws.some(k => k.toLowerCase().includes(_keyword.toLowerCase()))) return false;
    }
    return true;
  });
}

// --- Rendering helpers ---
function optionsLayout(options) {
  return options.every(o => (o.text || '').length <= 45) ? 'options-2col' : 'options-1col';
}

function renderOptions(options, answer, hideAnswer) {
  const layout = optionsLayout(options);
  const rows = options.map(o => {
    let cls = 'option';
    const isCorrect = o.key === answer;
    if (!hideAnswer && answer && answer !== 'X') {
      if (isCorrect) cls += ' correct';
    }
    const keyEl = `<span class="opt-key">${o.key}</span>`;
    const txt = o.mapping
      ? Object.entries(o.mapping).map(([k,v]) => `${k}→${v}`).join(', ')
      : (o.text || '');
    return `<div class="${cls}">${keyEl}<span class="opt-text">${esc(txt)}</span></div>`;
  }).join('');
  return `<div class="options-grid ${layout}">${rows}</div>`;
}

function renderAnswerRow(answer) {
  if (!answer) return '';
  if (answer === 'X') return `<div class="answer-row"><span class="omitted">⊘ Omitted / Disputed</span></div>`;
  return `<div class="answer-row">Answer: <span class="correct-ans">${answer}</span></div>`;
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderLangBlock(block, type, hideAnswer) {
  if (!block) return '';
  if (type === 'other') {
    return `<div class="raw-content">${esc(block.raw_content || '')}</div>`;
  }
  let html = '';
  const opts = block.options || [];

  if (type === 'simple_mcq') {
    html += `<p class="q-stem">${esc(block.stem || '')}</p>`;
  } else if (type === 'statement_mcq') {
    html += `<p class="q-stem">${esc(block.stem || '')}</p>`;
    const items = (block.statements || []).map(s => `<li><b>${esc(s.no)}.</b> ${esc(s.text)}</li>`).join('');
    html += `<ul class="statements">${items}</ul>`;
  } else if (type === 'match_the_following') {
    html += `<p class="q-stem">${esc(block.stem || '')}</p>`;
    if (block.match_style === 'tabular') {
      const leftRows = (block.left_column || []).map(r => `<tr><td>${esc(r.key)}</td><td>${esc(r.text)}</td></tr>`).join('');
      const rightRows = (block.right_column || []).map(r => `<tr><td>${esc(r.no)}</td><td>${esc(r.text)}</td></tr>`).join('');
      html += `<table class="match-table"><thead><tr><th>List I</th><th></th><th>List II</th><th></th></tr></thead><tbody>`;
      const maxRows = Math.max(block.left_column?.length || 0, block.right_column?.length || 0);
      for (let i = 0; i < maxRows; i++) {
        const l = block.left_column?.[i];
        const r = block.right_column?.[i];
        html += `<tr><td>${l ? esc(l.key) : ''}</td><td>${l ? esc(l.text) : ''}</td><td>${r ? esc(r.no) : ''}</td><td>${r ? esc(r.text) : ''}</td></tr>`;
      }
      html += `</tbody></table>`;
    } else {
      const items = (block.items || []).map(s => `<li><b>${esc(s.no)}.</b> ${esc(s.text)}</li>`).join('');
      html += `<ul class="match-items">${items}</ul>`;
    }
  } else if (type === 'assertion_reason') {
    html += `<div class="ar-block"><div class="ar-label">Assertion [A]</div><div class="ar-text">${esc(block.assertion || '')}</div></div>`;
    html += `<div class="ar-block"><div class="ar-label">Reason [R]</div><div class="ar-text">${esc(block.reason || '')}</div></div>`;
  } else if (type === 'chronological_order') {
    html += `<p class="q-stem">${esc(block.stem || '')}</p>`;
    const items = (block.items || []).map(s => `<li><b>${esc(s.no)}.</b> ${esc(s.text)}</li>`).join('');
    html += `<ul class="statements">${items}</ul>`;
  }

  if (type !== 'other') {
    html += renderOptions(opts, null, true);  // browse: options never auto-reveal in lang block
  }
  return html;
}

function renderCard(q, forQuiz) {
  const hideAns = forQuiz || document.body.classList.contains('hide-answers');
  let meta = `<span class="q-num">Q ${q.question_no}</span><span class="q-year">${q.year} · ${q.group}</span>`;
  if (q.disputed) meta += `<span class="badge-disputed">DISPUTED</span>`;
  if (q.question_type === 'other') meta += `<span class="badge-other">⚠ UNCLASSIFIED</span>`;

  let body = '';
  const showEn = (_lang === 'en' || _lang === 'both') && q.en;
  const showTa = (_lang === 'ta' || _lang === 'both') && q.ta;

  if (_lang === 'both' && showEn) body += `<div class="lang-block"><div class="lang-label">English</div>${renderLangBlock(q.en, q.question_type, hideAns)}</div>`;
  else if (showEn) body += renderLangBlock(q.en, q.question_type, hideAns);

  if (_lang === 'both' && showTa) body += `<div class="lang-block"><div class="lang-label">Tamil</div>${renderLangBlock(q.ta, q.question_type, hideAns)}</div>`;
  else if (showTa) body += renderLangBlock(q.ta, q.question_type, hideAns);

  // Show answer in browse mode (or "Omitted" for X)
  if (!forQuiz) {
    const activeBlock = q.en || q.ta;
    const opts = activeBlock?.options || [];
    const answerHtml = (!hideAns && q.answer_key) ? renderAnswerRow(q.answer_key) : '';
    body += answerHtml;
  }

  return `<div class="question-card"><div class="q-meta">${meta}</div>${body}</div>`;
}

// --- Browse ---
function renderBrowse() {
  _keyword = document.getElementById('keyword-input').value.trim();
  const filtered = getFiltered();
  document.getElementById('result-count').textContent = `Showing ${filtered.length} question${filtered.length !== 1 ? 's' : ''}`;
  document.getElementById('quiz-count').textContent = filtered.length;

  const hasAnswers = filtered.some(q => q.answer_key && q.answer_key !== 'X');
  const startBtn = document.getElementById('start-quiz-btn');
  startBtn.disabled = !hasAnswers || filtered.length === 0;

  document.getElementById('questions-grid').innerHTML = filtered.map(q => renderCard(q, false)).join('');

  if (window.MathJax) MathJax.typesetPromise?.();
}

// --- Quiz ---
function startQuiz() {
  const filtered = getFiltered().filter(q => q.answer_key && q.answer_key !== 'X');
  if (!filtered.length) return;

  const mode = confirm('One at a time?\n\nOK = One at a time\nCancel = Exam mode') ? 'one' : 'exam';
  _qMode = mode;
  _qs    = filtered;
  _idx   = 0;
  _score = 0;
  _ans   = {};

  document.getElementById('browse-container').style.display = 'none';
  document.getElementById('quiz-area').style.display = 'block';

  if (mode === 'one') showOne();
  else showExam();
}

function showOne() {
  const q = _qs[_idx];
  document.getElementById('quiz-score-label').textContent = `Q ${_idx + 1} / ${_qs.length}  ·  Score: ${_score}`;
  document.getElementById('quiz-next-btn').style.display = 'none';
  document.getElementById('quiz-submit-btn').style.display = 'none';

  const activeBlock = q.en || q.ta;
  const opts = (activeBlock?.options || []).map(o => {
    return `<div class="option" data-key="${o.key}" onclick="pickOne('${q.question_no}','${o.key}','${q.answer_key}',this)">
      <span class="opt-key">${o.key}</span>
      <span class="opt-text">${esc(o.mapping ? Object.entries(o.mapping).map(([k,v])=>`${k}→${v}`).join(', ') : (o.text || ''))}</span>
    </div>`;
  }).join('');
  const layout = optionsLayout(activeBlock?.options || []);

  let body = `<div class="q-meta"><span class="q-num">Q ${q.question_no}</span><span class="q-year">${q.year} · ${q.group}</span></div>`;
  if ((_lang === 'en' || _lang === 'both') && q.en) body += renderLangBlock({...q.en, options: []}, q.question_type, true);
  if ((_lang === 'ta' || _lang === 'both') && q.ta) body += renderLangBlock({...q.ta, options: []}, q.question_type, true);
  body += `<div class="options-grid ${layout}" id="one-opts">${opts}</div>`;
  body += `<div id="one-feedback"></div>`;

  document.getElementById('quiz-content').innerHTML = `<div class="question-card" style="max-width:700px">${body}</div>`;
}

function pickOne(qno, chosen, correct, el) {
  if (document.getElementById('one-opts').dataset.locked) return;
  document.getElementById('one-opts').dataset.locked = '1';
  const isRight = chosen === correct;
  if (isRight) _score++;
  document.querySelectorAll('#one-opts .option').forEach(o => {
    if (o.dataset.key === correct) o.classList.add('correct');
    else if (o.dataset.key === chosen && !isRight) o.classList.add('wrong');
    o.style.cursor = 'default';
  });
  document.getElementById('one-feedback').innerHTML = isRight
    ? `<p style="color:var(--correct);font-weight:600;margin-top:0.5rem">✓ Correct</p>`
    : `<p style="color:var(--wrong);font-weight:600;margin-top:0.5rem">✗ Incorrect — Answer: ${correct}</p>`;
  document.getElementById('quiz-score-label').textContent = `Q ${_idx + 1} / ${_qs.length}  ·  Score: ${_score}`;

  const nextBtn = document.getElementById('quiz-next-btn');
  nextBtn.style.display = 'inline-block';
  nextBtn.textContent = _idx < _qs.length - 1 ? 'Next →' : 'Finish';
}

function quizNext() {
  _idx++;
  if (_idx < _qs.length) { showOne(); return; }
  document.getElementById('quiz-content').innerHTML = `
    <div class="score-banner">Final Score: ${_score} / ${_qs.length} (${Math.round(_score/_qs.length*100)}%)</div>
    <button class="start-btn" onclick="exitQuiz()">Back to Browse</button>`;
  document.getElementById('quiz-next-btn').style.display = 'none';
  document.getElementById('quiz-score-label').textContent = '';
}

function showExam() {
  document.getElementById('quiz-submit-btn').style.display = 'inline-block';
  document.getElementById('quiz-score-label').textContent = `Exam mode — ${_qs.length} questions`;
  const cards = _qs.map((q, i) => {
    const activeBlock = q.en || q.ta;
    const opts = (activeBlock?.options || []).map(o => {
      return `<div class="option" data-key="${o.key}" data-qi="${i}" onclick="pickExam(${i},'${o.key}',this)">
        <span class="opt-key">${o.key}</span>
        <span class="opt-text">${esc(o.mapping ? Object.entries(o.mapping).map(([k,v])=>`${k}→${v}`).join(', ') : (o.text || ''))}</span>
      </div>`;
    }).join('');
    const layout = optionsLayout(activeBlock?.options || []);
    let body = `<div class="q-meta"><span class="q-num">Q ${q.question_no}</span><span class="q-year">${q.year} · ${q.group}</span></div>`;
    if ((_lang === 'en' || _lang === 'both') && q.en) body += renderLangBlock({...q.en, options:[]}, q.question_type, true);
    if ((_lang === 'ta' || _lang === 'both') && q.ta) body += renderLangBlock({...q.ta, options:[]}, q.question_type, true);
    body += `<div class="options-grid ${layout}" id="exam-opts-${i}">${opts}</div>`;
    return `<div class="question-card">${body}</div>`;
  }).join('');
  document.getElementById('quiz-content').innerHTML = cards;
}

function pickExam(qi, key, el) {
  if (document.getElementById(`exam-opts-${qi}`).dataset.locked) return;
  document.querySelectorAll(`#exam-opts-${qi} .option`).forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
  _ans[qi] = key;
}

function submitExam() {
  let correct = 0;
  _qs.forEach((q, i) => {
    const chosen = _ans[i];
    const rightKey = q.answer_key;
    document.querySelectorAll(`#exam-opts-${i} .option`).forEach(o => {
      if (o.dataset.key === rightKey) o.classList.add('correct');
      else if (o.dataset.key === chosen && chosen !== rightKey) o.classList.add('wrong');
      o.style.cursor = 'default';
    });
    document.getElementById(`exam-opts-${i}`).dataset.locked = '1';
    if (chosen === rightKey) correct++;
  });
  _score = correct;
  document.getElementById('quiz-submit-btn').style.display = 'none';
  document.getElementById('quiz-score-label').textContent = `Score: ${correct} / ${_qs.length} (${Math.round(correct/_qs.length*100)}%)`;
  const banner = `<div class="score-banner">Score: ${correct} / ${_qs.length} (${Math.round(correct/_qs.length*100)}%)</div>`;
  document.getElementById('quiz-content').insertAdjacentHTML('afterbegin', banner);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function exitQuiz() {
  document.getElementById('quiz-area').style.display = 'none';
  document.getElementById('browse-container').style.display = 'block';
  _qMode = null; _qs = []; _idx = 0; _score = 0; _ans = {};
  document.getElementById('quiz-next-btn').style.display = 'none';
  document.getElementById('quiz-submit-btn').style.display = 'none';
}

init();
</script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add lib/template.html
git commit -m "feat: add HTML template — all renderers, filters, language toggle, quiz mode"
```

---

## Task 11: `build_html.py`

**Files:**
- Create: `build_html.py`

- [ ] **Step 1: Write `build_html.py`**

```python
# build_html.py
import json
from pathlib import Path


def build_html(
    master_path: str = "master.json",
    syllabus_path: str = "tnpsc_general_studies_aptitude_mental_ability_syllabus.json",
    template_path: str = "lib/template.html",
    output_path: str = "tnpsc_pyqs.html",
) -> None:
    with open(master_path, encoding="utf-8") as f:
        master = json.load(f)

    with open(syllabus_path, encoding="utf-8") as f:
        syllabus = json.load(f)

    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    questions_json = json.dumps(master["questions"], ensure_ascii=False)
    syllabus_json  = json.dumps(syllabus, ensure_ascii=False)

    html = template.replace("__QUESTIONS_DATA__", questions_json)
    html = html.replace("__SYLLABUS_DATA__", syllabus_json)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Built {output_path} — {master['total_questions']} questions")


if __name__ == "__main__":
    build_html()
```

- [ ] **Step 2: Run to produce HTML (requires master.json)**

```bash
python build_html.py
```

Expected: `Built tnpsc_pyqs.html — N questions`

- [ ] **Step 3: Open HTML in browser and verify**

Open `tnpsc_pyqs.html` in a browser. Confirm:
- Questions render with group/year meta badges
- Language toggle switches between EN / TA / Both
- Group, Paper Type, Year, Subject filters work
- Quiz mode launches (one-at-a-time and exam mode)
- Correct answers revealed after selection or submission
- Disputed badge shows on disputed questions
- `other` type shows raw_content with ⚠ UNCLASSIFIED badge

- [ ] **Step 4: Commit**

```bash
git add build_html.py
git commit -m "feat: add build_html.py — injects master.json into template"
```

---

## Task 12: End-to-end smoke test

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run full pipeline on one real PDF**

```bash
python extract.py pdf/G1/TNPSC_Group1_2025_GeneralStudies.pdf --group G1 --year 2025 --paper-type gs
python merge.py
python build_html.py
```

Expected: `tnpsc_pyqs.html` created with questions from G1 2025 GS paper.

- [ ] **Step 3: Check for "other" type questions**

```bash
python -c "
import json
with open('data/G1_2025_gs.json') as f: d = json.load(f)
others = [q for q in d['questions'] if q.get('question_type') == 'other']
print(f'{len(others)} unclassified questions')
for q in others: print(f'  Q{q[\"question_no\"]} — image: {q[\"meta\"][\"image_ref\"]}')
"
```

If any `other` questions exist:
```bash
python reextract.py --json data/G1_2025_gs.json --type other
```

Review proposals in terminal. If new type confirmed, add to `prompt_extract.txt` enum and re-run `reextract.py --image <image_path> --json data/G1_2025_gs.json`.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete TNPSC PYQ pipeline — extract, merge, build HTML"
```

---

## Self-Review Against Spec

| Spec requirement | Task |
|---|---|
| Claude Sonnet (Anthropic) Vision API | Task 3 |
| PDF → images per page, skip existing | Task 2 |
| Per-PDF JSON with wrapper (group, year, paper_type, language_mode) | Task 7 |
| Bilingual `en`/`ta` blocks, nullable for language-only papers | Tasks 6, 7 |
| Answer key from tick mark in same PDF; `"X"` for omitted | Task 6 (prompt) |
| 6 question types including `other` catch-all | Task 6 (prompt) |
| `question_keywords` in each language block | Task 6 (prompt) |
| `meta.image_ref` per question | Task 4 |
| Resume support via state file | Tasks 4, 7 |
| `--retry-failed` flag | Task 7 |
| Two-pass re-extraction (`reextract.py`) | Task 8 |
| Syllabus-mapped unit/theme/topic tagging | Tasks 5, 6 |
| `merge.py` → `master.json`, duplicate ID check | Task 9 |
| HTML language toggle (EN / TA / Both) | Task 10 |
| 6 filter cards (Group multi, PaperType single, Year multi, Subject single, Keyword, Quiz) | Task 10 |
| Correct defaults (G1 only, GS, latest year) | Task 10 |
| 6 question type renderers + badges | Task 10 |
| Quiz mode: one-at-a-time + exam mode | Task 10 |
| `answer_key: "X"` shown as OMITTED | Task 10 |
| `build_html.py` single-file output | Task 11 |
