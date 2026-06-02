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
            merge_to_master(str(data_dir), str(data_dir / "master.json"))


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
