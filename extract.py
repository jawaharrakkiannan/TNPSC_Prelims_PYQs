# extract.py
"""
Usage:
  python extract.py pdf/G1/TNPSC_Group1_2025_GeneralStudies.pdf --group G1 --year 2025 --paper-type gs
  python extract.py ... --subgroup 1A
  python extract.py ... --retry-failed
  python extract.py ... --batch-size 8 --workers 5
"""
import argparse
import io
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from lib.api_client import extract_questions_from_image, extract_questions_from_images
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


def run_batch(batch: list[int], batch_images: list[str], prompt: str, total_pages: int):
    """Worker: send one batch to Claude. Returns (batch, batch_images, questions, error)."""
    label = f"[{batch[0]}-{batch[-1]}/{total_pages}]" if len(batch) > 1 else f"[{batch[0]}/{total_pages}]"
    names = ", ".join(Path(p).name for p in batch_images)
    print(f"  {label} Sending {len(batch)} page(s): {names} ...", flush=True)
    try:
        if len(batch) == 1:
            questions = extract_questions_from_image(batch_images[0], prompt)
        else:
            questions = extract_questions_from_images(batch_images, prompt)
        count = len(questions)
        if count:
            print(f"  {label} Response received -- {count} questions extracted", flush=True)
        else:
            print(f"  {label} Response received -- 0 questions (non-question page, skipped)", flush=True)
        return batch, batch_images, questions, None
    except ValueError as e:
        print(f"  {label} FAILED -- {e}", flush=True)
        return batch, batch_images, [], str(e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path")
    parser.add_argument("--group", required=True)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--paper-type", required=True, choices=["gs", "en", "ta"])
    parser.add_argument("--subgroup", default=None)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--batch-size", type=int, default=8, help="Pages per API call (default 8)")
    parser.add_argument("--workers", type=int, default=5, help="Parallel API calls (default 5)")
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

    with open("prompt_extract.txt", encoding="utf-8") as f:
        prompt_template = f.read()

    syllabus = load_syllabus(args.syllabus)
    syllabus_compact = build_compact_syllabus(syllabus)
    language_mode = LANGUAGE_MODE[args.paper_type]
    paper_info = f"Group={args.group}, Year={args.year}, PaperType={args.paper_type}, SubGroup={args.subgroup}"
    prompt = build_prompt(prompt_template, language_mode, paper_info, syllabus_compact)

    print(f"Converting PDF: {args.pdf_path}")
    image_paths = pdf_to_images(args.pdf_path, image_dir, image_prefix)
    total_pages = len(image_paths)
    print(f"  {total_pages} pages")

    state = load_or_create_state(state_path, args.group, args.year, args.paper_type, total_pages)

    if args.retry_failed:
        pages_to_process = list(state["failed_pages"])
        state["failed_pages"] = []
        print(f"Retrying {len(pages_to_process)} failed pages")
    else:
        done = set(state["processed_pages"])
        pages_to_process = [i for i in range(1, total_pages + 1) if i not in done]
        print(f"Processing {len(pages_to_process)} of {total_pages} pages "
              f"| batch={args.batch_size} workers={args.workers}")

    all_questions: list[dict] = []
    if Path(partial_path).exists():
        with open(partial_path, encoding="utf-8") as f:
            data = json.load(f)
            all_questions = data.get("questions", [])

    batches = [pages_to_process[i:i + args.batch_size]
               for i in range(0, len(pages_to_process), args.batch_size)]

    write_lock = threading.Lock()
    extracted_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_batch = {
            executor.submit(run_batch, batch, [image_paths[p - 1] for p in batch], prompt, total_pages): batch
            for batch in batches
        }

        for future in as_completed(future_to_batch):
            batch, batch_images, questions, error = future.result()

            with write_lock:
                if error:
                    for p in batch:
                        if p not in state["failed_pages"]:
                            state["failed_pages"].append(p)
                    save_state(state_path, state)
                    continue

                for q in questions:
                    raw_meta = q.get("meta") or {}
                    page_idx = int(raw_meta.get("page_index", 1))
                    page_idx = max(1, min(page_idx, len(batch)))
                    image_ref = batch_images[page_idx - 1]
                    enriched = enrich_question(
                        q,
                        group=args.group,
                        year=args.year,
                        paper_type=args.paper_type,
                        image_ref=image_ref,
                        subgroup=args.subgroup,
                    )
                    all_questions.append(enriched)

                extracted_count += len(questions)

                for p in batch:
                    if p not in state["processed_pages"]:
                        state["processed_pages"].append(p)
                    if p in state["failed_pages"]:
                        state["failed_pages"].remove(p)

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

    all_nums = sorted(set(q["question_no"] for q in all_questions))
    if all_nums:
        expected = list(range(all_nums[0], all_nums[-1] + 1))
        missing = [n for n in expected if n not in all_nums]
        if missing:
            print(f"\nWARNING: Missing question numbers: {missing}")

    if not state["failed_pages"]:
        if Path(partial_path).exists():
            Path(partial_path).rename(final_path)
        print(f"\nDone. {extracted_count} questions extracted -> {final_path}")
    else:
        print(f"\nPartial complete. Failed pages: {state['failed_pages']}. Run with --retry-failed.")

    print(f"Processed: {len(state['processed_pages'])}/{total_pages} pages | Failed: {len(state['failed_pages'])}")


if __name__ == "__main__":
    main()
