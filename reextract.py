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
