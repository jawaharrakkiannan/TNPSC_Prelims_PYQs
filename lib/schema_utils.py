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
