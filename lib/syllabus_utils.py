# lib/syllabus_utils.py
import json
from pathlib import Path


def load_syllabus(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_compact_syllabus(syllabus: dict) -> str:
    """Build a compact text reference for the extraction prompt.
    Format per unit:
        Unit N: Name
          Theme: ThemeName → Topics: topic1, topic2, ...
    """
    lines = []
    for unit in syllabus["units"]:
        lines.append(f"Unit {unit['unit']}: {unit['name']}")
        for theme in unit["themes"]:
            topic_names = [t["topic_name"] for t in theme["topics"]]
            lines.append(f"  Theme: {theme['theme']} → Topics: {', '.join(topic_names)}")
    return "\n".join(lines)
