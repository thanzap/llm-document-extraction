"""Duplicate detection, shared by every way a document arrives
(manual upload, phone camera scan, email attachment).

Two levels, cheapest first:

1. Same file: SHA-256 of the incoming bytes, checked *before* storage and AI
   extraction, so a re-sent file costs nothing.
2. Same invoice, different file (a photo and the PDF of one receipt): matched
   on ΜΑΡΚ (the national e-invoicing registration number) when known,
   otherwise on counterparty VAT + invoice number + date.

In production the candidates come from Postgres; here they are any iterable of
objects with `mark`, `counterparty_vat`, `invoice_numbers` and `invoice_date`.
"""

import hashlib
import re
from collections.abc import Iterable


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_mark(value) -> str | None:
    """ΜΑΡΚ is a long integer (typically 15 digits). Anything else is ignored."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if 9 <= len(digits) <= 20 else None


def _norm_vat(value) -> str:
    return re.sub(r"\D", "", str(value or ""))  # "EL 123456789" → "123456789"


# Greek capitals that look identical to Latin ones: invoice series are typed
# either way ("Α 102" vs "A-102").
_LOOKALIKES = str.maketrans("ΑΒΕΖΗΙΚΜΝΟΡΤΥΧ", "ABEZHIKMNOPTYX")


def normalize_number(value) -> str:
    """'ΤΔΑ 1256', 'τδα-1256' → 'TΔA1256' with look-alike letters unified."""
    return re.sub(r"[\W_]+", "", str(value or "")).upper().translate(_LOOKALIKES)


def find_duplicate(
    existing: Iterable,
    *,
    mark: str | None = None,
    counterparty_vat: str | None = None,
    invoice_numbers=None,
    invoice_date: str | None = None,
) -> tuple | None:
    """(existing invoice, human-readable reason) or None."""
    existing = list(existing)
    if mark:
        for inv in existing:
            if inv.mark == mark:
                return inv, f"same ΜΑΡΚ {mark}"

    vat = _norm_vat(counterparty_vat)
    numbers = {normalize_number(n) for n in (invoice_numbers or []) if normalize_number(n)}
    if not (vat and numbers and invoice_date):
        return None
    for inv in existing:
        if inv.invoice_date != invoice_date or _norm_vat(inv.counterparty_vat) != vat:
            continue
        if numbers & {normalize_number(n) for n in (inv.invoice_numbers or [])}:
            return inv, "same supplier/customer VAT, invoice number and date"
    return None
