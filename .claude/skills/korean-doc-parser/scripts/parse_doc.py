#!/usr/bin/env python3
"""Dispatch a document file to the right parser by extension and print extracted text.

Usage: python3 parse_doc.py <path> [--ocr]

Supported: .hwp .hwpx .pdf .doc .docx
--ocr forces PaddleOCR page-image OCR (for scanned/image-only PDFs).
"""
import sys
import argparse
from pathlib import Path


def parse_hwp_hwpx(path: Path) -> str:
    from hwp_hwpx_parser import HWP5Reader, HWPXReader

    reader_cls = HWPXReader if path.suffix.lower() == ".hwpx" else HWP5Reader
    reader = reader_cls(str(path))
    result = reader.extract()
    return result.text


def parse_docx(path: Path) -> str:
    import docx

    doc = docx.Document(str(path))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def parse_doc_legacy(path: Path) -> str:
    import mammoth

    with open(path, "rb") as f:
        result = mammoth.extract_raw_text(f)
    return result.value


def parse_pdf(path: Path, force_ocr: bool = False) -> str:
    import fitz

    doc = fitz.open(str(path))
    text_parts = []
    needs_ocr_pages = []
    for i, page in enumerate(doc):
        text = page.get_text()
        if force_ocr or len(text.strip()) < 10:
            needs_ocr_pages.append(i)
        text_parts.append(text)

    if needs_ocr_pages:
        ocr_text = ocr_pdf_pages(doc, needs_ocr_pages)
        for i, t in ocr_text.items():
            text_parts[i] = t

    return "\n".join(text_parts)


def ocr_pdf_pages(doc, page_indices) -> dict:
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(use_angle_cls=True, lang="korean")
    results = {}
    for i in page_indices:
        page = doc[i]
        pix = page.get_pixmap(dpi=300)
        img_path = f"/tmp/_ocr_page_{i}.png"
        pix.save(img_path)
        result = ocr.ocr(img_path, cls=True)
        lines = []
        for block in result or []:
            for line in block or []:
                lines.append(line[1][0])
        results[i] = "\n".join(lines)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--ocr", action="store_true", help="force OCR on PDF pages")
    args = ap.parse_args()

    path = Path(args.path)
    ext = path.suffix.lower()

    if ext in (".hwp", ".hwpx"):
        text = parse_hwp_hwpx(path)
    elif ext == ".docx":
        text = parse_docx(path)
    elif ext == ".doc":
        text = parse_doc_legacy(path)
    elif ext == ".pdf":
        text = parse_pdf(path, force_ocr=args.ocr)
    else:
        raise SystemExit(f"unsupported extension: {ext}")

    print(text)


if __name__ == "__main__":
    main()
