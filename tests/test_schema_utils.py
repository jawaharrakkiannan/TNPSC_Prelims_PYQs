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
