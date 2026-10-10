"""Evidence ingestion: content-hash dedupe and bounded passage splitting.

PDF extraction uses pypdf (a small, pure-Python dependency). Scanned,
password-protected or otherwise unsupported PDFs produce an explicit
limitation string instead of silently failing or guessing content; OCR is
out of scope for this milestone.
"""
import hashlib
import io

PASSAGE_CHARS = 2000


def content_hash(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def split_passages(text: str, max_chars: int = PASSAGE_CHARS) -> list[str]:
    text = text.strip()
    if not text:
        return []
    passages = []
    for start in range(0, len(text), max_chars):
        chunk = text[start:start + max_chars].strip()
        if chunk:
            passages.append(chunk)
    return passages


def passages_with_page_numbers(pages: list[str], max_chars: int = PASSAGE_CHARS) -> list[dict]:
    """Chunk each PDF page's text independently, preserving the true page
    number for every chunk (``chunk_index`` numbers chunks within a page).

    A single long page split into several chunks is still cited as one page,
    not as several distinct pages.
    """
    passages: list[dict] = []
    for page_number, page_text in enumerate(pages, start=1):
        chunks = split_passages(page_text, max_chars)
        for chunk_index, chunk in enumerate(chunks, start=1):
            passages.append({"page_number": page_number, "chunk_index": chunk_index, "text": chunk})
    return passages


def plain_text_passages(text: str, max_chars: int = PASSAGE_CHARS) -> list[dict]:
    """Plain-text (non-PDF) evidence has no real page concept; everything is
    attributed to a single logical page 1, with sequential chunk indices."""
    return [
        {"page_number": 1, "chunk_index": chunk_index, "text": chunk}
        for chunk_index, chunk in enumerate(split_passages(text, max_chars), start=1)
    ]


def extract_pdf_text(raw_bytes: bytes) -> tuple[list[str], str]:
    """Returns (page_texts, limitations). Never raises for a readable PDF file."""
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        return [], "PDF extraction dependency is not installed; upload as plain text instead."
    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
    except PdfReadError:
        return [], "Unsupported or corrupted PDF file."
    if getattr(reader, "is_encrypted", False):
        try:
            if reader.decrypt("") == 0:
                return [], "Password-protected PDF could not be opened; supply plain text instead."
        except Exception:
            return [], "Password-protected PDF could not be opened; supply plain text instead."
    pages: list[str] = []
    empty_pages = 0
    for page in reader.pages:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if not text:
            empty_pages += 1
        pages.append(text)
    limitations = ""
    if empty_pages:
        limitations = (
            f"{empty_pages} of {len(pages)} page(s) returned no extractable text "
            "(likely scanned images); OCR is out of scope for this milestone."
        )
    return pages, limitations
