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
