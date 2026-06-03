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

    print(f"Built {output_path} -- {master['total_questions']} questions")


if __name__ == "__main__":
    build_html()
    # index.html = v1 browse view (serves Vercel root without needing route rewrites)
    build_html(output_path="index.html")
    if Path("lib/template_v2.html").exists():
        build_html(template_path="lib/template_v2.html", output_path="tnpsc_pyqs_v2.html")
        build_html(template_path="lib/template_v2.html", output_path="v2.html")
    if Path("lib/template_v3.html").exists():
        build_html(template_path="lib/template_v3.html", output_path="tnpsc_pyqs_v3.html")
        build_html(template_path="lib/template_v3.html", output_path="v3.html")
