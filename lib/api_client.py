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


def _extract_json(text: str) -> str:
    """Extract JSON array or object from response, handling prose preamble and code fences."""
    text = text.strip()
    # Strip code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    # Find first JSON array or object start in case Claude added prose before it
    match = re.search(r"(\[|\{)", text)
    if match:
        text = text[match.start():]
    return text


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
            parsed = json.loads(_extract_json(raw))
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
