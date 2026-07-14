---
name: korean-doc-parser
description: Parse HWP, HWPX, PDF, DOC, and DOCX files — including Korean OCR for scanned pages. Use whenever a task involves reading a Korean government/legal document attachment (법제처, 식약처, 국가법령정보센터 등) or any .hwp/.hwpx file, or a PDF that turns out to be scanned images rather than selectable text.
---

# Korean document parser

Extracts text from Korean-heavy document formats that the built-in Read tool
cannot open directly.

## Coverage

| Format | Library | Notes |
|---|---|---|
| `.hwp` (binary HWP5) | `hwp-hwpx-parser` | pure Python, no JVM |
| `.hwpx` (OOXML-based) | `hwp-hwpx-parser` | tables/images/footnotes supported |
| `.docx` | `python-docx` | paragraphs + tables |
| `.doc` (legacy binary) | `mammoth` | raw text extraction |
| `.pdf` | `PyMuPDF` (`fitz`) | falls back to OCR per-page if a page has near-no extractable text |
| scanned/image pages | `PaddleOCR` (`lang="korean"`) | highest published Korean OCR accuracy among CPU-installable FOSS options; `Tesseract` + `kor` traineddata installed as a lighter fallback (`tesseract -l kor <img>`) |

## Usage

```bash
python3 .claude/skills/korean-doc-parser/scripts/parse_doc.py /path/to/file.hwpx
python3 .claude/skills/korean-doc-parser/scripts/parse_doc.py /path/to/scanned.pdf --ocr
```

Prints extracted plain text to stdout. For tables, DOCX/HWPX rows are
tab-joined per line.

## Known limitations

- DRM/password-protected HWP files are not supported by any FOSS library.
- Deeply nested tables or embedded OLE objects may extract imperfectly —
  spot-check output against the source for high-stakes text (e.g. legal
  definitions) rather than trusting it blindly.
- PaddleOCR downloads its model weights on first real OCR call; if the
  network policy blocks the model-hub host, OCR calls will fail even though
  `import paddleocr` succeeds. Fall back to `tesseract -l kor` in that case.
