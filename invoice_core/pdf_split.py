"""Split a PDF that holds several separate documents (typically a stack of
receipts run through the office scanner in one go) into one PDF per document.

Gemini looks at the whole PDF once and says where each document starts and
ends; a multi-page invoice stays together. Anything unclear → no split, the
file is handled as one document exactly as before."""

import io

from pypdf import PdfReader, PdfWriter

MAX_PAGES = 150  # beyond this, don't try (cost/time); handled as one document

SYSTEM = (
    "You look at a scanned PDF from a Greek accounting office. It may be ONE document, or "
    "SEVERAL separate documents scanned one after another (invoices, receipts, credit notes, "
    "ΑΠΥ/ΤΙΜ/ΤΔΑ etc.). Decide where each separate document starts and ends.\n"
    "Rules:\n"
    "- A document that continues over several pages (page 1/2, 2/2; items continuing; totals on "
    "the last page; same issuer, number and date) is ONE document.\n"
    "- A new issuer, a new document number or a new header means a new document.\n"
    "- Completely blank pages and scanner separator sheets belong to no document: list them in 'skip'.\n"
    "- Every page number from 1 to N must appear exactly once, either inside one document range "
    "or in 'skip'.\n"
    'Answer JSON only: {"documents": [{"from": 1, "to": 2}, {"from": 3, "to": 3}], "skip": []}'
)


def page_count(data: bytes) -> int:
    try:
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return 0


def _valid_ranges(answer: dict, n: int) -> list[tuple] | None:
    """Ranges sorted by page, covering 1..n exactly once with the skipped pages."""
    try:
        ranges = sorted((int(d["from"]), int(d["to"])) for d in answer.get("documents") or [])
        skip = {int(p) for p in answer.get("skip") or []}
    except (TypeError, ValueError, KeyError):
        return None
    seen = set(skip)
    for a, b in ranges:
        if not (1 <= a <= b <= n):
            return None
        pages = set(range(a, b + 1))
        if pages & seen:
            return None
        seen |= pages
    if seen != set(range(1, n + 1)) or not ranges:
        return None
    return ranges


def find_documents(data: bytes, ask_json) -> list[tuple] | None:
    """Page ranges (1-based, inclusive) of the separate documents, or None
    when it's a single document or the answer can't be trusted.

    `ask_json(contents, system_instruction, max_output_tokens)` is injected so
    the model call can be swapped or faked in tests (see invoice_core.extraction)."""
    n = page_count(data)
    if n < 2 or n > MAX_PAGES:
        return None
    from google.genai import types  # imported lazily: the rest of the module has no SDK dependency

    answer = ask_json(
        [types.Part.from_bytes(data=data, mime_type="application/pdf"),
         f"This PDF has {n} pages. Where does each separate document start and end?"],
        SYSTEM, max_output_tokens=2048,
    )
    ranges = _valid_ranges(answer if isinstance(answer, dict) else {}, n)
    return ranges if ranges and len(ranges) > 1 else None


def split(data: bytes, ranges: list[tuple]) -> list[bytes]:
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for a, b in ranges:
        writer = PdfWriter()
        for i in range(a - 1, b):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        parts.append(buf.getvalue())
    return parts
