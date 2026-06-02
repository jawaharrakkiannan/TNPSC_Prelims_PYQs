# lib/pdf_utils.py
from pathlib import Path
from pdf2image import convert_from_path


def pdf_to_images(pdf_path: str, output_dir: str, prefix: str, dpi: int = 200) -> list[str]:
    """Convert each PDF page to a PNG. Returns list of image paths.
    Skips pages where the output file already exists (resume-safe).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    pages = convert_from_path(pdf_path, dpi=dpi)
    paths = []
    for i, page in enumerate(pages, 1):
        fname = f"{prefix}_p{i:03d}.png"
        fpath = out / fname
        if not fpath.exists():
            page.save(str(fpath), "PNG")
        paths.append(str(fpath))
    return paths
