"""Extract the text layer from published PDF attachments.

The public Atlas website publishes research papers, reports, and playbooks as
PDFs. Those carry knowledge that exists nowhere else in the corpus, so the
ingestion job reads their text layer rather than merely cataloguing the files.

Extraction is deliberately text-layer only. A scanned, image-only PDF raises
:class:`PdfTextUnavailable` instead of producing an empty document, so the run
reports it as a source failure that an operator can act on.
"""

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError


LOGGER = logging.getLogger(__name__)

# pypdf emits one advisory warning per embedded CFF font when the optional
# fontTools package is absent. The published corpus extracts correctly without
# it, and a report-sized PDF can log dozens of these, so only this sub-logger
# is quieted; every other pypdf warning still surfaces.
logging.getLogger("pypdf._cmap").setLevel(logging.ERROR)

# Below this, a "text layer" is page furniture (a header, a page number) rather
# than readable content, and the file needs OCR that this job does not perform.
MINIMUM_EXTRACTED_CHARACTERS = 200


class PdfTextUnavailable(ValueError):
    """The file could not yield usable text without OCR or a password."""


def extract_pdf_text(data, max_pages=500):
    """Return (text, page_count, warnings) for one PDF's byte content."""
    if not isinstance(data, (bytes, bytearray)):
        raise PdfTextUnavailable("PDF content must be bytes")
    if not data:
        raise PdfTextUnavailable("PDF content is empty")

    warnings = []
    try:
        reader = PdfReader(io.BytesIO(bytes(data)))
    except (PdfReadError, ValueError, OSError) as error:
        raise PdfTextUnavailable(
            f"PDF could not be parsed: {type(error).__name__}: {error}"
        ) from error

    if reader.is_encrypted:
        # An empty user password is common for print-restricted files and is
        # worth one attempt; a real password is not something we can supply.
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            raise PdfTextUnavailable("PDF is password protected")
        warnings.append("decrypted with an empty user password")

    try:
        page_count = len(reader.pages)
    except (PdfReadError, ValueError) as error:
        raise PdfTextUnavailable(
            f"PDF page tree is unreadable: {error}"
        ) from error

    selected_pages = min(page_count, max_pages)
    if page_count > selected_pages:
        warnings.append(
            f"truncated to the first {selected_pages} of {page_count} pages"
        )

    parts = []
    failed_pages = 0
    for index in range(selected_pages):
        try:
            parts.append(reader.pages[index].extract_text() or "")
        except Exception as error:
            # One malformed page should not discard the rest of the document.
            failed_pages += 1
            LOGGER.debug("PDF page %d could not be read: %s", index + 1, error)
    if failed_pages:
        warnings.append(f"{failed_pages} page(s) could not be read")

    text = "\n\n".join(part.strip() for part in parts if part.strip())
    if len(text.strip()) < MINIMUM_EXTRACTED_CHARACTERS:
        raise PdfTextUnavailable(
            "PDF has no usable text layer and would need OCR "
            f"({len(text.strip())} characters across {selected_pages} pages)"
        )

    return text, page_count, warnings
