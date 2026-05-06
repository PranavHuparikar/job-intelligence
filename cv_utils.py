"""
cv_utils.py — CV format handler for the Job Intelligence System.

Supports:
  - PDF  → pdfplumber (multi-column aware, preserves structure)
  - DOCX → python-docx (paragraph + table text)
  - TXT  → plain UTF-8 read

Two entry points:
  read_cv(path: str)  → str          for CLI / main.py (file path)
  read_cv_bytes(data, filename) → str for Streamlit (in-memory bytes)

SECURITY NOTE: CV bytes are processed in memory and never written to disk.
"""

import re
from pathlib import Path


# ── Public API ────────────────────────────────────────────────────────────────

def read_cv(path: str) -> str:
    """
    Read a CV from a file path. Supports .pdf, .docx, .txt.
    Returns cleaned plain text.
    Raises FileNotFoundError or ValueError on bad input.
    """
    p = Path(path.strip())
    if not p.exists():
        raise FileNotFoundError(f"CV file not found: {p}")

    ext = p.suffix.lower()
    if ext == ".pdf":
        return _read_pdf_path(p)
    elif ext == ".docx":
        return _read_docx_path(p)
    elif ext in (".txt", ".md", ""):
        return _clean(_read_txt_path(p))
    else:
        raise ValueError(
            f"Unsupported CV format '{ext}'. "
            "Please use PDF, DOCX, or TXT."
        )


def read_cv_bytes(data: bytes, filename: str) -> str:
    """
    Read a CV from in-memory bytes (e.g. Streamlit file_uploader).
    filename is used only to determine format.
    Returns cleaned plain text.

    SECURITY: bytes are never written to disk.
    """
    import io

    ext = Path(filename).suffix.lower()
    stream = io.BytesIO(data)

    if ext == ".pdf":
        return _read_pdf_stream(stream)
    elif ext == ".docx":
        return _read_docx_stream(stream)
    elif ext in (".txt", ".md", ""):
        return _clean(data.decode("utf-8", errors="replace"))
    else:
        raise ValueError(
            f"Unsupported CV format '{ext}'. "
            "Please upload PDF, DOCX, or TXT."
        )


def word_count(text: str) -> int:
    return len(text.split())


# ── PDF ───────────────────────────────────────────────────────────────────────

def _read_pdf_path(path: Path) -> str:
    _check_pdfplumber()
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        return _extract_pdf_pages(pdf)


def _read_pdf_stream(stream) -> str:
    _check_pdfplumber()
    import pdfplumber
    with pdfplumber.open(stream) as pdf:
        return _extract_pdf_pages(pdf)


_PDF_PAGE_LIMIT = 30  # refuse to process monster PDFs that could cause OOM


def _extract_pdf_pages(pdf) -> str:
    total_pages = len(pdf.pages)
    if total_pages > _PDF_PAGE_LIMIT:
        raise ValueError(
            f"PDF has {total_pages} pages — the limit is {_PDF_PAGE_LIMIT}. "
            "Please upload a trimmed version of your CV (usually 1–3 pages)."
        )

    pages = []
    for page in pdf.pages:
        text = page.extract_text(x_tolerance=2, y_tolerance=3)
        if text:
            pages.append(text)

    combined = _clean("\n".join(pages))

    # Detect image-only (scanned) PDFs: pages exist but no text was extracted
    if not combined and total_pages > 0:
        raise ValueError(
            "This PDF appears to be a scanned image — no text layer was found. "
            "Please export your CV as a text-based PDF (File → Save As PDF from "
            "Word/Google Docs), or paste the text directly into the CV field."
        )

    return combined


def _check_pdfplumber():
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        raise ImportError(
            "pdfplumber is required for PDF CVs. "
            "Run: pip install pdfplumber"
        )


# ── DOCX ──────────────────────────────────────────────────────────────────────

def _read_docx_path(path: Path) -> str:
    _check_docx()
    from docx import Document
    doc = Document(str(path))
    return _clean(_extract_docx(doc))


def _read_docx_stream(stream) -> str:
    _check_docx()
    from docx import Document
    doc = Document(stream)
    return _clean(_extract_docx(doc))


def _extract_docx(doc) -> str:
    """Extract text from paragraphs and tables in order."""
    parts = []
    for block in doc.element.body:
        tag = block.tag.split("}")[-1] if "}" in block.tag else block.tag
        if tag == "p":
            from docx.oxml.ns import qn
            # r.text can be None for some XML nodes — filter before joining
            text = "".join(
                r.text for r in block.iter(qn("w:t")) if r.text is not None
            )
            if text.strip():
                parts.append(text.strip())
        elif tag == "tbl":
            # Extract table cells row by row
            from docx.oxml.ns import qn
            for row in block.iter(qn("w:tr")):
                cells = []
                for cell in row.iter(qn("w:tc")):
                    cell_text = "".join(
                        t.text for t in cell.iter(qn("w:t")) if t.text is not None
                    ).strip()
                    if cell_text:
                        cells.append(cell_text)
                if cells:
                    parts.append(" | ".join(cells))
    return "\n".join(parts)


def _check_docx():
    try:
        from docx import Document  # noqa: F401
    except ImportError:
        raise ImportError(
            "python-docx is required for DOCX CVs. "
            "Run: pip install python-docx"
        )


# ── TXT ───────────────────────────────────────────────────────────────────────

def _read_txt_path(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ── Shared cleanup ────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """
    Normalise whitespace:
    - Remove null bytes
    - Collapse 3+ blank lines → 2
    - Collapse 2+ spaces on a line → 1
    - Strip leading/trailing whitespace
    """
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
