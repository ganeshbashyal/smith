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
