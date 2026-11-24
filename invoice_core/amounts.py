"""Parsing and normalising the free-form values an LLM extracts from invoices.

Model output is never trusted as-is: amounts arrive as '1.234,56 €' or 1234.56,
dates as '25/12/2026' or 'September 3, 2026', and per-rate VAT lines in several
shapes. Everything is normalised here before it touches the books.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%m/%d/%Y")
_SPELLED_FORMATS = ("%B %d, %Y", "%d %B %Y", "%b %d, %Y", "%d %b %Y")


def parse_date(value) -> date | None:
    """Best-effort parse of an extracted invoice date (numeric or spelled-out)."""
    if not value:
        return None
    s = str(value).strip()
    for fmt in _DATE_FORMATS + _SPELLED_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)  # last resort: a leading ISO-like date
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return None


def normalize_invoice_date(value) -> str:
    """DD/MM/YYYY (Greek accounting convention). Unparseable input is kept
    verbatim rather than lost, so a human can fix it during review."""
    d = parse_date(value)
    return d.strftime("%d/%m/%Y") if d else (str(value).strip() if value else "")


def parse_amount(value) -> Decimal | None:
    """'1.234,56 €' → Decimal('1234.56'); '1234.56' → Decimal('1234.56')."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    s = re.sub(r"[^\d.,-]", "", str(value))
    if not s:
        return None
    if "," in s and "." in s:  # both present → '.' thousands, ',' decimal (EU)
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _rate_num(rate) -> Decimal | None:
    # 0 is a real rate (VAT-exempt), not "missing".
    s = re.sub(r"[^\d.]", "", "" if rate is None else str(rate))
    try:
        return Decimal(s) if s else None
    except InvalidOperation:
        return None


CLASSIFICATION_KEYS = ("mydata_category", "e3_code")
_STRUCTURED_KEYS = ("rate", "net", "tax", "amount", "account_code")


def normalize_tax_lines(value, subtotal=None, vat_total=None, default_account=None):
    """Normalise per-rate VAT lines to [{rate, net, tax, account_code, ...}].

    Accepts every shape the field has had: structured dicts, {"24%": "1.35"}
    mappings, "24%:3.25" strings. Net is derived from tax/rate when missing.
    With nothing usable, falls back to one line built from subtotal + VAT total.
    """
    lines = []

    def add(rate, net, tax, account_code, extra=None):
        r = _rate_num(rate)
        tax_d = parse_amount(tax)
        net_d = parse_amount(net)
        if net_d is None and tax_d is not None and r:
            net_d = (tax_d / (r / Decimal(100))).quantize(Decimal("0.01"))
        lines.append({
            "rate": str(r) if r is not None else "",
            "net": f"{net_d:.2f}" if net_d is not None else "",
            "tax": f"{tax_d:.2f}" if tax_d is not None else "",
            "account_code": account_code or default_account or "",
            **(extra or {}),
        })

    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                if any(k in item for k in _STRUCTURED_KEYS):
                    tax = item.get("tax")
                    if tax is None:
                        tax = item.get("amount")
                    extra = {k: item[k] for k in CLASSIFICATION_KEYS if item.get(k)}
                    add(item.get("rate"), item.get("net"), tax, item.get("account_code"), extra)
                else:
                    for k, v in item.items():
                        add(k, None, v, None)
            elif isinstance(item, str) and ":" in item:
                k, v = item.split(":", 1)
                add(k, None, v, None)
    elif isinstance(value, dict):
        for k, v in value.items():
            add(k, None, v, None)

    if not lines:
        add("", subtotal, vat_total, default_account)
    return lines


def sum_tax(lines) -> Decimal:
    total = Decimal("0")
    for ln in lines or []:
        amt = parse_amount(ln.get("tax")) if isinstance(ln, dict) else None
        if amt:
            total += amt
    return total
