"""
TNPSC PDF extraction pipeline.
Stage 1: mistral-ocr-2512 (OCR)
Stage 2: mistral-large-2512 | claude-haiku-4-5 | gpt-4o-mini (JSON extraction)

Usage:
  python extract_mistral.py pdf/G1/TNPSC_Group1_2023.pdf --group G1 --year 2023 --paper-type gs
  python extract_mistral.py pdf/G1/... --group G1 --year 2024 --paper-type gs --stage2-model claude
  python extract_mistral.py pdf/G1/... --group G1 --year 2024 --paper-type gs --stage2-model openai --workers 6
"""
import argparse
import base64
import collections
import concurrent.futures
import io
import json
import os
import re
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(override=True)

from mistralai.client import Mistral
from lib.schema_utils import enrich_question, LANGUAGE_MODE
from lib.syllabus_utils import build_compact_syllabus, load_syllabus
from build_html import build_html

OCR_MODEL      = "mistral-ocr-2512"
OCR_CACHE_DIR  = "data/ocr_cache"
MAX_REC_PAGES  = 16
TPM_LIMIT      = 50_000
TPM_MARGIN     = 0.90


class _Page:
    """Lightweight stand-in for mistral OCR page objects when loading from cache."""
    __slots__ = ("markdown",)
    def __init__(self, markdown): self.markdown = markdown


def save_ocr_cache(tag, pages):
    os.makedirs(OCR_CACHE_DIR, exist_ok=True)
    path = os.path.join(OCR_CACHE_DIR, f"{tag}_pages.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([p.markdown or "" for p in pages], f, ensure_ascii=False)
    return path


def load_ocr_cache(tag):
    path = os.path.join(OCR_CACHE_DIR, f"{tag}_pages.json")
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [_Page(md) for md in data], path
RPS_LIMIT  = 1.0

STAGE2_DEFAULTS = {
    "mistral": "mistral-large-2512",
    "claude":  "claude-haiku-4-5-20251001",
    "openai":  "gpt-4o-mini",
    "gemini":  "gemini-2.0-flash",
}

PARSE_PROMPT = """\
You are extracting questions from a TNPSC exam paper.
The text below is OCR-extracted markdown from {NUM_PAGES} consecutive pages.

PAPER INFO: Group={GROUP}, Year={YEAR}, PaperType={PAPER_TYPE}
LANGUAGE MODE: {LANGUAGE_MODE}
{LANG_RULE}

QUESTION TYPES:
1. simple_mcq
2. statement_mcq
3. match_the_following
4. assertion_reason
5. chronological_order
6. other (use raw_content)

Return a JSON array. Raw JSON only — no markdown, no code fences.

SCHEMA per question:
{{
  "question_no": <int>,
  "question_type": "<type>",
  "unit_id": <int 1-8>,
  "unit": "<exact unit name>",
  "theme": "<exact theme name>",
  "topic": "<exact topic_name>",
  "disputed": <bool>,
  "stem_has_blank": <bool>,
  "has_math": <bool>,
  "math_format": <"plain_text"|"latex"|"unicode"|null>,
  "options_layout": <"single_column"|"two_column">,
  "media": null,
  "en": <lang block or null>,
  "ta": <lang block or null>,
  "answer_key": <"A"|"B"|"C"|"D"|"E"|"X"|null>,
  "meta": {{"extraction_status":"extracted","ocr_confidence":null,"notes":"","page_index":1}}
}}

LANG BLOCK — simple_mcq:
{{"stem":"...","question_keywords":[...],"options":[{{"key":"A","text":"..."}},...]}}\

LANG BLOCK — statement_mcq:
{{"stem":"...","question_keywords":[...],"item_numbering_style":"roman|arabic|alpha","statements":[{{"no":"i","text":"..."}}],"options":[...]}}\

LANG BLOCK — match_the_following (tabular):
{{"stem":"...","question_keywords":[...],"match_style":"tabular","left_column":[{{"key":"a","text":"..."}}],"right_column":[{{"no":1,"text":"..."}}],"options":[{{"key":"A","mapping":{{"a":5,"b":4,"c":1,"d":3}}}},...]}}\

LANG BLOCK — match_the_following (inline):
{{"stem":"...","question_keywords":[...],"match_style":"inline","items":[{{"no":"1","text":"..."}}],"options":[...]}}\

LANG BLOCK — assertion_reason:
{{"assertion":"...","reason":"...","question_keywords":[...],"options":[...]}}\

LANG BLOCK — chronological_order:
{{"stem":"...","question_keywords":[...],"item_numbering_style":"roman|arabic","items":[{{"no":"1","text":"..."}}],"options":[...]}}\

LANG BLOCK — other: {{"raw_content":"<verbatim>"}}\

ANSWER KEY RULES (CRITICAL — read carefully):
- The OCR renders a tick/check mark as ☑ (U+2611, ballot box with check).
- If ☑ appears immediately before or after an option letter/text, that option is the answer.
- If a question mentions it is "referred to expert committee", "placed before expert committee",
  "to be decided by committee", or similar → set disputed=true AND answer_key="X".
- If NO ☑ appears anywhere in the question's OCR text → answer_key=null.
- NEVER use your own knowledge to guess the correct answer.
- NEVER infer the answer from the question content.
- Only report what is physically marked in the OCR text.

SYLLABUS:
{SYLLABUS_COMPACT}

NON-QUESTION PAGES: return [] for cover/instructions/OMR/blank pages.
Return ONLY the JSON array.

OCR TEXT:
{OCR_TEXT}
"""

LANG_RULES = {
    "bilingual": "Each question has English followed immediately by Tamil. Extract BOTH into \"en\" and \"ta\" blocks.",
    "en_only":   "All questions are in English only. Populate \"en\" block. Set \"ta\" to null.",
    "ta_only":   "All questions are in Tamil only. Populate \"ta\" block. Set \"en\" to null.",
}


# ---------------------------------------------------------------------------
# Rate limiter (Mistral only)
# ---------------------------------------------------------------------------

class TpmRateLimiter:
    """Rolling 60-second token window. Call wait() before each request."""
    def __init__(self, tpm_limit, margin=0.90, rps_limit=1.0):
        self.effective_tpm = int(tpm_limit * margin)
        self.min_gap = 1.0 / rps_limit
        self._window = collections.deque()
        self._last_req = 0.0

    def wait(self, estimated_tokens: int = 0):
        now = time.time()
        gap = now - self._last_req
        if gap < self.min_gap:
            time.sleep(self.min_gap - gap)
            now = time.time()

        cutoff = now - 60.0
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

        used = sum(t for _, t in self._window)
        available = self.effective_tpm - used

        if estimated_tokens > 0 and available < estimated_tokens:
            wait_needed = 0.0
            if self._window:
                oldest_ts = self._window[0][0]
                wait_needed = max(0.0, (oldest_ts + 60.0) - now)
            if wait_needed > 0:
                print(f"  [rate-limit] TPM headroom low ({available} < {estimated_tokens} est.), sleeping {wait_needed:.1f}s ...", flush=True)
                time.sleep(wait_needed)
                now = time.time()
                cutoff = now - 60.0
                while self._window and self._window[0][0] < cutoff:
                    self._window.popleft()

        self._last_req = time.time()

    def record(self, tokens: int):
        self._window.append((time.time(), tokens))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def validate_answer_keys(questions, full_ocr_text):
    """
    Deterministically override answer_key by scanning OCR text for ☑ (U+2611).
    Finds which question and which option each mark belongs to.
    Any question without a ☑ in OCR → answer_key=null.
    """
    import bisect
    MARK = '☑'

    if MARK not in full_ocr_text:
        for q in questions:
            q['answer_key'] = None
        return questions

    # Build sorted list of (char_pos, question_no) for every question start.
    # Pattern: line starting with "N." or "N)" where N is 1-300.
    q_positions = []
    for m in re.finditer(r'(?m)^\s*(\d{1,3})[.)]\s', full_ocr_text):
        q_no = int(m.group(1))
        if 1 <= q_no <= 300:
            q_positions.append((m.start(), q_no))
    q_positions.sort()
    pos_list = [p for p, _ in q_positions]

    ocr_answers = {}
    for mark_m in re.finditer(re.escape(MARK), full_ocr_text):
        pos = mark_m.start()

        # Last question start before this ☑
        idx = bisect.bisect_right(pos_list, pos) - 1
        if idx < 0:
            continue
        q_no = q_positions[idx][1]

        # Find option letter in next 80 chars after ☑
        snippet = full_ocr_text[pos: pos + 80]
        opt_m = re.search(r'[\(\s]?([A-E])[\)\s.]', snippet)
        if opt_m:
            ocr_answers[q_no] = opt_m.group(1)

    before = sum(1 for q in questions if q.get('answer_key') and q['answer_key'] != 'X')
    for q in questions:
        q_no = q.get('question_no')
        if q.get('answer_key') == 'X':
            pass  # keep disputed marker
        else:
            q['answer_key'] = ocr_answers.get(q_no)
    after = sum(1 for q in questions if q.get('answer_key') and q['answer_key'] != 'X')
    print(f"  [answer-validate] OCR marks found: {len(ocr_answers)} | "
          f"LLM had {before} letters → corrected to {after}", flush=True)
    return questions


def extract_json_str(text):
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()
    m = re.search(r'(\[|\{)', text)
    if m:
        text = text[m.start():]
    return text


def parse_raw(raw):
    text = extract_json_str(raw)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        import json_repair
        result = json.loads(json_repair.repair_json(text))
    if isinstance(result, dict) and "questions" in result:
        result = result["questions"]
    # Flatten if json_repair produced a nested list
    if result and isinstance(result[0], list):
        result = [q for sub in result for q in sub]
    # Drop non-dict items
    return [q for q in result if isinstance(q, dict)]


# ---------------------------------------------------------------------------
# Provider-specific batch callers
# ---------------------------------------------------------------------------

def call_mistral(client, model, prompt, label, limiter, est_tokens):
    limiter.wait(estimated_tokens=est_tokens)
    print(f"  {label} sending ...", flush=True)
    chunks = []
    in_tok = out_tok = 0
    for attempt in range(1, 4):
        try:
            with client.chat.stream(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                timeout_ms=120_000,
            ) as stream:
                for event in stream:
                    delta = event.data.choices[0].delta.content if (
                        event.data.choices and event.data.choices[0].delta.content
                    ) else None
                    if delta:
                        chunks.append(delta)
                    if hasattr(event.data, "usage") and event.data.usage:
                        u = event.data.usage
                        in_tok  = getattr(u, "prompt_tokens", 0) or 0
                        out_tok = getattr(u, "completion_tokens", 0) or 0
            raw = "".join(chunks)
            if not in_tok:
                in_tok  = len(prompt) // 4
                out_tok = len(raw) // 4
            limiter.record(in_tok + out_tok)
            return raw, in_tok, out_tok
        except Exception as e:
            if attempt < 3:
                print(f"  {label} attempt {attempt} failed ({type(e).__name__}: {e}), retrying in 10s ...", flush=True)
                time.sleep(10)
            else:
                print(f"  {label} FAILED after 3 attempts, skipping.", flush=True)
                return "", 0, 0


def call_claude(client, model, prompt, label):
    print(f"  {label} sending ...", flush=True)
    for attempt in range(1, 4):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
            raw    = resp.content[0].text
            in_tok = resp.usage.input_tokens
            out_tok = resp.usage.output_tokens
            return raw, in_tok, out_tok
        except Exception as e:
            if attempt < 3:
                print(f"  {label} attempt {attempt} failed ({type(e).__name__}: {e}), retrying in 10s ...", flush=True)
                time.sleep(10)
            else:
                print(f"  {label} FAILED after 3 attempts, skipping.", flush=True)
                return "", 0, 0


def call_openai(client, model, prompt, label):
    print(f"  {label} sending ...", flush=True)
    for attempt in range(1, 4):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                timeout=120,
            )
            raw    = resp.choices[0].message.content
            in_tok = resp.usage.prompt_tokens
            out_tok = resp.usage.completion_tokens
            return raw, in_tok, out_tok
        except Exception as e:
            if attempt < 3:
                print(f"  {label} attempt {attempt} failed ({type(e).__name__}: {e}), retrying in 10s ...", flush=True)
                time.sleep(10)
            else:
                print(f"  {label} FAILED after 3 attempts, skipping.", flush=True)
                return "", 0, 0


def call_gemini(client, model, prompt, label):
    print(f"  {label} sending ...", flush=True)
    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(model=model, contents=prompt)
            raw     = resp.text
            usage   = resp.usage_metadata
            in_tok  = getattr(usage, "prompt_token_count", 0) or 0
            out_tok = getattr(usage, "candidates_token_count", 0) or 0
            return raw, in_tok, out_tok
        except Exception as e:
            if attempt < 3:
                wait = 30 if "429" in str(e) or "quota" in str(e).lower() else 10
                print(f"  {label} attempt {attempt} failed ({type(e).__name__}: {e}), retrying in {wait}s ...", flush=True)
                time.sleep(wait)
            else:
                print(f"  {label} FAILED after 3 attempts, skipping.", flush=True)
                return "", 0, 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path")
    parser.add_argument("--group", required=True)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--paper-type", required=True, choices=["gs", "en", "ta"])
    parser.add_argument("--subgroup", default=None)
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Pages per batch (default: 12 for mistral, 6 for claude/openai)")
    parser.add_argument("--stage2-model", choices=["mistral", "claude", "openai", "gemini"], default="mistral",
                        help="LLM provider for Stage 2 JSON extraction (default: mistral)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel workers for claude/openai (ignored for mistral)")
    parser.add_argument("--syllabus", default="tnpsc_general_studies_aptitude_mental_ability_syllabus.json")
    parser.add_argument("--local", action="store_true", help="Build local HTML only (not committed)")
    parser.add_argument("--no-answer-key", action="store_true",
                        help="Set all answer_key fields to null (skip OCR mark detection)")
    parser.add_argument("--language-mode", choices=["bilingual", "en_only", "ta_only"], default=None,
                        help="Override language mode (default: derived from --paper-type)")
    parser.add_argument("--force-ocr", action="store_true",
                        help="Re-run OCR even if a page cache exists")
    parser.add_argument("--no-recovery", action="store_true",
                        help="Skip gap-recovery re-queries after first extraction pass")
    args = parser.parse_args()

    provider   = args.stage2_model
    chat_model = STAGE2_DEFAULTS[provider]
    batch_size = args.batch_size or (12 if provider == "mistral" else 4)
    # Gemini free tier: 15 RPM — cap workers to avoid rate limit
    if provider == "gemini" and args.workers > 3:
        args.workers = 3

    tag      = f"{args.group}_{args.subgroup}_{args.year}_{args.paper_type}" if args.subgroup else f"{args.group}_{args.year}_{args.paper_type}"
    data_out = f"data/{tag}_mistral.json"
    v1_out   = f"local_{tag}_v1.html" if args.local else f"tnpsc_{tag.lower()}_v1.html"
    v3_out   = f"local_{tag}_v3.html" if args.local else f"tnpsc_{tag.lower()}_v3.html"

    language_mode = args.language_mode or LANGUAGE_MODE[args.paper_type]
    lang_rule     = LANG_RULES[language_mode]

    # Init clients
    mistral_client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
    stage2_client  = None
    if provider == "claude":
        import anthropic
        stage2_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    elif provider == "openai":
        import openai as oai
        stage2_client = oai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    elif provider == "gemini":
        from google import genai as google_genai
        stage2_client = google_genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    else:
        stage2_client = mistral_client

    syllabus        = load_syllabus(args.syllabus)
    syllabus_compact = build_compact_syllabus(syllabus)
    limiter         = TpmRateLimiter(TPM_LIMIT, TPM_MARGIN, RPS_LIMIT)

    # ------------------------------------------------------------------
    # Step 1: OCR  (uses cache when available)
    # ------------------------------------------------------------------
    print(f"Step 1: OCR {args.pdf_path} via {OCR_MODEL} ...", flush=True)
    cached_pages, cache_path = (None, None) if args.force_ocr else load_ocr_cache(tag)
    if cached_pages:
        pages = cached_pages
        full_ocr_text = "\n".join(p.markdown for p in pages)
        mark_count = full_ocr_text.count('☑')
        print(f"  Loaded {len(pages)} pages from cache ({cache_path}) | ☑ marks: {mark_count}", flush=True)
    else:
        with open(args.pdf_path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        size_mb = os.path.getsize(args.pdf_path) / 1024 / 1024
        limiter.wait()
        t0 = time.time()
        resp = mistral_client.ocr.process(
            model=OCR_MODEL,
            document={
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{b64}",
                "document_name": os.path.basename(args.pdf_path),
            },
        )
        limiter.record(0)
        pages = resp.pages
        full_ocr_text = "\n".join(p.markdown or '' for p in pages)
        mark_count = full_ocr_text.count('☑')
        print(f"  Done in {time.time()-t0:.1f}s | {len(pages)} pages | {size_mb:.1f} MB | ☑ marks: {mark_count}", flush=True)
        saved = save_ocr_cache(tag, pages)
        print(f"  OCR cache saved -> {saved}", flush=True)

    avg_ocr_chars        = sum(len(p.markdown or '') for p in pages) / max(len(pages), 1)
    est_in_per_page      = avg_ocr_chars / 3.5
    est_out_per_page     = 1_200
    est_tokens_per_batch = int((est_in_per_page + est_out_per_page) * batch_size + 3_000)
    print(f"  Est. tokens/batch ({batch_size} pages): ~{est_tokens_per_batch:,}", flush=True)
    if provider == "mistral":
        batches_per_min = (TPM_LIMIT * TPM_MARGIN) / est_tokens_per_batch
        print(f"  TPM budget -> ~{batches_per_min:.1f} batches/min", flush=True)

    # ------------------------------------------------------------------
    # Step 2: JSON extraction
    # ------------------------------------------------------------------
    batches = [pages[i:i+batch_size] for i in range(0, len(pages), batch_size)]
    print(f"\nStep 2: JSON extraction via {chat_model} (batch={batch_size}"
          + (f", workers={args.workers}" if provider != "mistral" else "")
          + ") ...", flush=True)

    def make_prompt(bi, batch):
        page_start = (bi - 1) * batch_size + 1
        ocr_text = "\n\n".join(
            f"--- PAGE {page_start + i} ---\n{p.markdown or ''}"
            for i, p in enumerate(batch)
        )
        return PARSE_PROMPT.format(
            NUM_PAGES=len(batch),
            GROUP=args.group, YEAR=args.year, PAPER_TYPE=args.paper_type,
            LANGUAGE_MODE=language_mode, LANG_RULE=lang_rule,
            SYLLABUS_COMPACT=syllabus_compact,
            OCR_TEXT=ocr_text,
        )

    def make_label(bi, batch):
        page_start = (bi - 1) * batch_size + 1
        page_end   = page_start + len(batch) - 1
        return f"[pages {page_start}-{page_end}/{len(pages)}]"

    total_in = total_out = 0
    batch_results = [None] * len(batches)   # (raw, in_tok, out_tok) per batch

    if provider == "mistral":
        for bi, batch in enumerate(batches, 1):
            prompt = make_prompt(bi, batch)
            label  = make_label(bi, batch)
            raw, in_tok, out_tok = call_mistral(
                stage2_client, chat_model, prompt, label, limiter, est_tokens_per_batch
            )
            batch_results[bi - 1] = (raw, in_tok, out_tok)
            total_in  += in_tok
            total_out += out_tok
            print(f"  {label} tokens: in={in_tok} out={out_tok} | cumulative: {total_in+total_out:,}", flush=True)

    else:
        def worker(idx_batch):
            idx, batch = idx_batch
            bi     = idx + 1
            prompt = make_prompt(bi, batch)
            label  = make_label(bi, batch)
            if provider == "claude":
                return idx, call_claude(stage2_client, chat_model, prompt, label)
            elif provider == "openai":
                return idx, call_openai(stage2_client, chat_model, prompt, label)
            else:
                return idx, call_gemini(stage2_client, chat_model, prompt, label)

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(worker, (i, b)) for i, b in enumerate(batches)]
            for future in concurrent.futures.as_completed(futures):
                idx, (raw, in_tok, out_tok) = future.result()
                batch_results[idx] = (raw, in_tok, out_tok)
                total_in  += in_tok
                total_out += out_tok
                label = make_label(idx + 1, batches[idx])
                print(f"  {label} tokens: in={in_tok} out={out_tok} | cumulative: {total_in+total_out:,}", flush=True)

    # ------------------------------------------------------------------
    # Parse + enrich
    # ------------------------------------------------------------------
    all_questions = []
    # batch_info: (page_lo_0based, page_hi_0based, [q_nos]) — used for gap recovery
    batch_info = []
    for idx, (raw, _, _) in enumerate(batch_results):
        page_lo = idx * batch_size
        page_hi = min(page_lo + batch_size, len(pages)) - 1
        bi    = idx + 1
        batch = batches[idx]
        label = make_label(bi, batch)
        if not raw:
            print(f"  {label} skipped (empty response)", flush=True)
            batch_info.append((page_lo, page_hi, []))
            continue
        try:
            parsed = parse_raw(raw)
        except Exception as e:
            print(f"  {label} JSON parse FAILED: {e}", flush=True)
            parsed = []

        q_nos_batch = [q.get("question_no") for q in parsed
                       if isinstance(q.get("question_no"), int)]
        batch_info.append((page_lo, page_hi, q_nos_batch))

        for q in parsed:
            enriched = enrich_question(q, group=args.group, year=args.year,
                                       paper_type=args.paper_type, image_ref=args.pdf_path,
                                       subgroup=args.subgroup)
            all_questions.append(enriched)
        print(f"  {label} {len(parsed)} questions | running total: {len(all_questions)}", flush=True)

    # Dedup + sort
    seen, deduped = {}, []
    for q in all_questions:
        if q["question_no"] not in seen:
            seen[q["question_no"]] = True
            deduped.append(q)
    deduped.sort(key=lambda q: q["question_no"])

    all_nums = sorted(q["question_no"] for q in deduped)

    # ------------------------------------------------------------------
    # Step 2.5: Gap recovery (up to 2 passes)
    # ------------------------------------------------------------------
    if not args.no_recovery:
        page_data = [p.markdown or "" for p in pages]
        q_page_map = sorted(
            (q, pl, ph)
            for pl, ph, q_nos in batch_info
            for q in q_nos
        )

        for recovery_pass in range(2):
            cur_nums    = sorted(q["question_no"] for q in deduped)
            missing_set = set(range(cur_nums[0], cur_nums[-1] + 1)) - set(cur_nums) if cur_nums else set()
            if not missing_set:
                break

            ms = sorted(missing_set)
            gaps, s, p = [], ms[0], ms[0]
            for n in ms[1:]:
                if n != p + 1:
                    gaps.append((s, p)); s = n
                p = n
            gaps.append((s, p))

            pass_label = f" (pass {recovery_pass + 1})" if recovery_pass > 0 else ""
            print(f"\nStep 2.5{pass_label}: Gap recovery — {len(gaps)} gap(s): {gaps}", flush=True)
            recovered_qs = []

            for gap_start, gap_end in gaps:
                before = [(q, pl, ph) for q, pl, ph in q_page_map if q < gap_start]
                after  = [(q, pl, ph) for q, pl, ph in q_page_map if q > gap_end]

                page_lo = before[-1][1] if before else 0
                page_hi = after[0][2]   if after  else len(page_data) - 1

                # Cap page window
                if page_hi - page_lo + 1 > MAX_REC_PAGES:
                    mid = (page_lo + page_hi) // 2
                    page_lo = max(0, mid - MAX_REC_PAGES // 2)
                    page_hi = min(len(page_data) - 1, page_lo + MAX_REC_PAGES - 1)

                page_start_1 = page_lo + 1
                rec_pages    = page_data[page_lo: page_hi + 1]
                ocr_text     = "\n\n".join(
                    f"--- PAGE {page_start_1 + i} ---\n{md}"
                    for i, md in enumerate(rec_pages)
                )
                prompt = PARSE_PROMPT.format(
                    NUM_PAGES=len(rec_pages),
                    GROUP=args.group, YEAR=args.year, PAPER_TYPE=args.paper_type,
                    LANGUAGE_MODE=language_mode, LANG_RULE=lang_rule,
                    SYLLABUS_COMPACT=syllabus_compact,
                    OCR_TEXT=f"[RECOVERY: find questions {gap_start}–{gap_end} only]\n\n{ocr_text}",
                )
                label = f"[recovery Q{gap_start}-{gap_end} pages {page_lo+1}-{page_hi+1}]"
                print(f"  {label} sending ...", flush=True)

                if provider == "mistral":
                    raw, in_tok, out_tok = call_mistral(stage2_client, chat_model, prompt, label, limiter, 0)
                elif provider == "claude":
                    raw, in_tok, out_tok = call_claude(stage2_client, chat_model, prompt, label)
                elif provider == "openai":
                    raw, in_tok, out_tok = call_openai(stage2_client, chat_model, prompt, label)
                else:
                    raw, in_tok, out_tok = call_gemini(stage2_client, chat_model, prompt, label)

                total_in  += in_tok
                total_out += out_tok

                if not raw:
                    print(f"  {label} empty response, skipping", flush=True)
                    continue
                try:
                    parsed = parse_raw(raw)
                except Exception as e:
                    print(f"  {label} parse failed: {e}", flush=True)
                    parsed = []

                new_q_nos = []
                for q in parsed:
                    q_no = q.get("question_no")
                    if isinstance(q_no, int) and q_no in missing_set:
                        enriched = enrich_question(q, group=args.group, year=args.year,
                                                   paper_type=args.paper_type, image_ref=args.pdf_path,
                                                   subgroup=args.subgroup)
                        recovered_qs.append(enriched)
                        new_q_nos.append(q_no)
                print(f"  {label} recovered {len(new_q_nos)}: {sorted(new_q_nos)}", flush=True)

            if not recovered_qs:
                break  # No improvement; stop early

            all_questions_extended = list(deduped) + recovered_qs
            seen2, deduped = {}, []
            for q in all_questions_extended:
                if q["question_no"] not in seen2:
                    seen2[q["question_no"]] = True
                    deduped.append(q)
            deduped.sort(key=lambda q: q["question_no"])

            # Update q_page_map with newly recovered Q#s for next pass
            recovered_nums = set(q["question_no"] for q in recovered_qs)
            existing_in_map = {q for q, _, _ in q_page_map}
            # Use midpoint of gap window as approximate page position
            for gap_start, gap_end in gaps:
                before = [(q, pl, ph) for q, pl, ph in q_page_map if q < gap_start]
                after  = [(q, pl, ph) for q, pl, ph in q_page_map if q > gap_end]
                page_lo = before[-1][1] if before else 0
                page_hi = after[0][2]   if after  else len(page_data) - 1
                mid_page = (page_lo + page_hi) // 2
                for q_no in range(gap_start, gap_end + 1):
                    if q_no in recovered_nums and q_no not in existing_in_map:
                        q_page_map.append((q_no, mid_page, mid_page))
            q_page_map.sort()

    # Merge with existing JSON: preserve any questions found in previous runs
    if os.path.exists(data_out) and not args.force_ocr:
        try:
            with open(data_out, encoding="utf-8") as f:
                existing = json.load(f)
            existing_nos = {q["question_no"] for q in existing.get("questions", [])}
            new_nos      = {q["question_no"] for q in deduped}
            carry_over   = [q for q in existing.get("questions", []) if q["question_no"] not in new_nos]
            if carry_over:
                print(f"\nMerge: carrying {len(carry_over)} Q#s from previous run: "
                      f"{sorted(q['question_no'] for q in carry_over)}", flush=True)
                merged = sorted(deduped + carry_over, key=lambda q: q["question_no"])
                deduped = merged
        except Exception as e:
            print(f"\nMerge: skipped (could not read existing JSON: {e})", flush=True)

    final_nums    = sorted(q["question_no"] for q in deduped)
    still_missing = [n for n in range(final_nums[0], final_nums[-1] + 1) if n not in set(final_nums)] if final_nums else []
    if still_missing:
        print(f"\nWARNING: Missing question numbers: {still_missing}", flush=True)

    # Answer key handling
    if args.no_answer_key:
        for q in deduped:
            q['answer_key'] = None
        print("\nAnswer keys cleared (--no-answer-key).", flush=True)
    else:
        print("\nValidating answer keys against OCR marks ...", flush=True)
        deduped = validate_answer_keys(deduped, full_ocr_text)

    os.makedirs("data", exist_ok=True)
    master = {
        "group": args.group, "subgroup": args.subgroup,
        "year": args.year, "paper_type": args.paper_type,
        "language_mode": language_mode, "paper_code": "",
        "total_questions": len(deduped),
        "extraction_model": f"{OCR_MODEL} + {chat_model}",
        "questions": deduped,
    }
    with open(data_out, "w", encoding="utf-8") as f:
        json.dump(master, f, indent=2, ensure_ascii=False)

    print(f"\nTotal API tokens: {total_in+total_out:,} (in={total_in:,} out={total_out:,})")
    print(f"Saved {len(deduped)} questions -> {data_out}")

    # Step 3: Build HTMLs
    print("\nStep 3: Building HTMLs ...", flush=True)
    build_html(master_path=data_out, output_path=v1_out)
    build_html(master_path=data_out, template_path="lib/template_v3.html", output_path=v3_out)
    print(f"  {v1_out}")
    print(f"  {v3_out}")


if __name__ == "__main__":
    main()
