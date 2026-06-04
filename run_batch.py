"""
run_batch.py  —  Sequential batch extractor for G2/G4 papers.
Runs extract_mistral.py for every paper defined in PAPERS, prints a summary table.

Usage:
  python run_batch.py                  # run all
  python run_batch.py --group G2       # only G2
  python run_batch.py --group G4       # only G4
  python run_batch.py --dry-run        # print commands only, don't run
  python run_batch.py --skip-existing  # skip if data JSON already exists
"""
import argparse
import json
import os
import subprocess
import sys
import time

BASE   = os.path.dirname(os.path.abspath(__file__))
COMMON = ["--stage2-model", "openai", "--workers", "4", "--no-answer-key", "--local"]

# ---------------------------------------------------------------------------
# Paper definitions
# ---------------------------------------------------------------------------
# Each entry: (pdf_relpath, group, year, paper_type, subgroup, language_mode_override)
# subgroup=None  → no --subgroup flag
# language_mode_override=None  → use paper_type default

PAPERS = [
    # ── G2 bilingual years (GS=bilingual 200Q, en/ta=100Q each) ──────────────
    ("pdf/G2/TNPSC_Group2_2013_General_Studies.pdf",  "G2", 2013, "gs", None,  None),
    ("pdf/G2/TNPSC_Group2_2013_General_English.pdf",  "G2", 2013, "en", None,  None),
    ("pdf/G2/TNPSC_Group2_2013_General_Tamil.pdf",    "G2", 2013, "ta", None,  None),

    ("pdf/G2/TNPSC_Group2_2015_General_Studies.pdf",  "G2", 2015, "gs", None,  None),
    ("pdf/G2/TNPSC_Group2_2015_General_English.pdf",  "G2", 2015, "en", None,  None),
    ("pdf/G2/TNPSC_Group2_2015_General_Tamil.pdf",    "G2", 2015, "ta", None,  None),

    ("pdf/G2/TNPSC_Group2_2017_General_Studies.pdf",  "G2", 2017, "gs", None,  None),
    ("pdf/G2/TNPSC_Group2_2017_General_English.pdf",  "G2", 2017, "en", None,  None),
    ("pdf/G2/TNPSC_Group2_2017_General_Tamil.pdf",    "G2", 2017, "ta", None,  None),

    ("pdf/G2/TNPSC_Group2_2018_General_Studies.pdf",  "G2", 2018, "gs", None,  None),
    ("pdf/G2/TNPSC_Group2_2018_General_English.pdf",  "G2", 2018, "en", None,  None),
    ("pdf/G2/TNPSC_Group2_2018_General_Tamil.pdf",    "G2", 2018, "ta", None,  None),

    ("pdf/G2/TNPSC_Group2_2022_GeneralStudies.pdf",   "G2", 2022, "gs", None,  None),
    ("pdf/G2/TNPSC_Group2_2022_General_English.pdf",  "G2", 2022, "en", None,  None),
    ("pdf/G2/TNPSC_Group2_2022_General_Tamil.pdf",    "G2", 2022, "ta", None,  None),

    # ── G2 2024 — separate English/Tamil medium GS papers (200Q monolingual) ─
    ("pdf/G2/TNPSC_Group2_2024_GeneralStudies_English.pdf", "G2", 2024, "gs", "en", "en_only"),
    ("pdf/G2/TNPSC_Group2_2024_GeneralStudies_Tamil.pdf",   "G2", 2024, "gs", "ta", "ta_only"),

    # ── G2 2025 ───────────────────────────────────────────────────────────────
    ("pdf/G2/TNPSC_Group2_2025_GeneralStudies_English.pdf", "G2", 2025, "gs", "en", "en_only"),
    ("pdf/G2/TNPSC_Group2_2025_GeneralStudies_Tamil.pdf",   "G2", 2025, "gs", "ta", "ta_only"),

    # ── G3 2023 (lives in G2 folder) ─────────────────────────────────────────
    ("pdf/G2/TNPSC_Group3_2023_GeneralStudies_English.pdf", "G3", 2023, "gs", "en", "en_only"),
    ("pdf/G2/TNPSC_Group3_2023_GeneralStudies_Tamil.pdf",   "G3", 2023, "gs", "ta", "ta_only"),

    # ── G4 bilingual years ────────────────────────────────────────────────────
    ("pdf/G4/TNPSC_Group4_2012_GeneralStudies.pdf",   "G4", 2012, "gs", None,  None),
    ("pdf/G4/TNPSC_Group4_2012_General_English.pdf",  "G4", 2012, "en", None,  None),
    ("pdf/G4/TNPSC_Group4_2012_General_Tamil.pdf",    "G4", 2012, "ta", None,  None),

    ("pdf/G4/TNPSC_Group4_2016_GeneralStudies.pdf",   "G4", 2016, "gs", None,  None),
    ("pdf/G4/TNPSC_Group4_2016_General_English.pdf",  "G4", 2016, "en", None,  None),
    ("pdf/G4/TNPSC_Group4_2016_General_Tamil.pdf",    "G4", 2016, "ta", None,  None),

    ("pdf/G4/TNPSC_Group4_2018_GeneralStudies.pdf",   "G4", 2018, "gs", None,  None),
    ("pdf/G4/TNPSC_Group4_2018_General_English.pdf",  "G4", 2018, "en", None,  None),
    ("pdf/G4/TNPSC_Group4_2018_General_Tamil.pdf",    "G4", 2018, "ta", None,  None),

    ("pdf/G4/TNPSC_Group4_2019_GeneralStudies.pdf",   "G4", 2019, "gs", None,  None),
    ("pdf/G4/TNPSC_Group4_2019_General_English.pdf",  "G4", 2019, "en", None,  None),
    ("pdf/G4/TNPSC_Group4_2019_General_Tamil.pdf",    "G4", 2019, "ta", None,  None),

    # ── G4 2022 — Tamil-only GS ───────────────────────────────────────────────
    ("pdf/G4/TNPSC_Group4_2022_GeneralStudies_Tamil.pdf",   "G4", 2022, "gs", None,  "ta_only"),

    # ── G4 2024 ───────────────────────────────────────────────────────────────
    ("pdf/G4/TNPSC_Group4_2024_GeneralStudies_English.pdf", "G4", 2024, "gs", "en", "en_only"),
    ("pdf/G4/TNPSC_Group4_2024_GeneralStudies_Tamil.pdf",   "G4", 2024, "gs", "ta", "ta_only"),

    # ── G4 2025 ───────────────────────────────────────────────────────────────
    ("pdf/G4/TNPSC_Group4_2025_GeneralStudies_English.pdf", "G4", 2025, "gs", "en", "en_only"),
    ("pdf/G4/TNPSC_Group4_2025_GeneralStudies_Tamil.pdf",   "G4", 2025, "gs", "ta", "ta_only"),
]


def make_tag(group, year, paper_type, subgroup):
    if subgroup:
        return f"{group}_{subgroup}_{year}_{paper_type}"
    return f"{group}_{year}_{paper_type}"


def build_cmd(pdf, group, year, paper_type, subgroup, lang_mode):
    cmd = [sys.executable, "extract_mistral.py", pdf,
           "--group", group,
           "--year", str(year),
           "--paper-type", paper_type]
    if subgroup:
        cmd += ["--subgroup", subgroup]
    if lang_mode:
        cmd += ["--language-mode", lang_mode]
    cmd += COMMON
    return cmd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=["G2", "G3", "G4"], default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    papers = PAPERS
    if args.group:
        papers = [p for p in papers if p[1] == args.group]

    results = []
    total = len(papers)

    for i, (pdf, group, year, paper_type, subgroup, lang_mode) in enumerate(papers, 1):
        tag      = make_tag(group, year, paper_type, subgroup)
        data_out = f"data/{tag}_mistral.json"
        cmd      = build_cmd(pdf, group, year, paper_type, subgroup, lang_mode)

        print(f"\n[{i}/{total}] {tag}", flush=True)
        print(f"  cmd: {' '.join(cmd)}", flush=True)

        if args.dry_run:
            results.append((tag, "dry-run", 0))
            continue

        if args.skip_existing and os.path.exists(data_out):
            print(f"  SKIP — {data_out} exists", flush=True)
            results.append((tag, "skipped", 0))
            continue

        t0 = time.time()
        proc = subprocess.run(cmd, cwd=BASE, capture_output=False)
        elapsed = time.time() - t0

        if proc.returncode != 0:
            results.append((tag, "FAILED", elapsed))
            continue

        # Read result counts
        try:
            with open(os.path.join(BASE, data_out), encoding="utf-8") as f:
                master = json.load(f)
            n = master.get("total_questions", len(master.get("questions", [])))
            nums = [q["question_no"] for q in master.get("questions", [])]
            missing = ([x for x in range(min(nums), max(nums)+1) if x not in nums]
                       if nums else [])
            status = f"{n}Q missing={missing}" if missing else f"{n}Q OK"
        except Exception as e:
            status = f"read-err:{e}"

        results.append((tag, status, elapsed))

    # Summary
    print("\n" + "="*60)
    print(f"{'TAG':<30} {'STATUS':<30} {'TIME':>6}")
    print("="*60)
    for tag, status, elapsed in results:
        t = f"{elapsed:.0f}s" if elapsed else "-"
        print(f"{tag:<30} {status:<30} {t:>6}")
    print("="*60)
    failures = [r for r in results if "FAILED" in r[1]]
    print(f"\n{len(results)-len(failures)}/{len(results)} succeeded")


if __name__ == "__main__":
    main()
