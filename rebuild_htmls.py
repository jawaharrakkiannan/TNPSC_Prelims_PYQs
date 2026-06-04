"""Rebuild all local HTML files from current data JSON files."""
import glob
import os
from build_html import build_html

data_files = sorted(glob.glob("data/*_mistral.json"))
for path in data_files:
    fname = os.path.basename(path)
    tag = fname.replace("_mistral.json", "")
    v1 = f"local_{tag}_v1.html"
    v3 = f"local_{tag}_v3.html"
    build_html(master_path=path, output_path=v1)
    build_html(master_path=path, template_path="lib/template_v3.html", output_path=v3)

print(f"\nRebuilt {len(data_files) * 2} HTML files ({len(data_files)} papers).")
