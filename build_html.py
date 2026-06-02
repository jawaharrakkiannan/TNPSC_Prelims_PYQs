# build_html.py
import json
from pathlib import Path


def build_html(
    master_path: str = "master.json",
    syllabus_path: str = "tnpsc_general_studies_aptitude_mental_ability_syllabus.json",
    template_path: str = "lib/template.html",
    output_path: str = "tnpsc_pyqs.html",
) -> None:
    with open(master_path, encoding="utf-8") as f:
        master = json.load(f)

    with open(syllabus_path, encoding="utf-8") as f:
        syllabus = json.load(f)

    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    questions_json = json.dumps(master["questions"], ensure_ascii=False)
    syllabus_json  = json.dumps(syllabus, ensure_ascii=False)

    html = template.replace("__QUESTIONS_DATA__", questions_json)
    html = html.replace("__SYLLABUS_DATA__", syllabus_json)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Built {output_path} — {master['total_questions']} questions")


if __name__ == "__main__":
    build_html()
