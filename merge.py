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
