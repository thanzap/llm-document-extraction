"""Builds the bulk-import .xlsx workbook that Greek accounting software expects,
from a client's validated invoices.

Spec highlights:
  * Sheets: Εγγραφές-Άρθρα (entries), Λογαριασμοί (accounts),
    Συναλλασσόμενοι (counterparties), plus a Πελάτης (client) info sheet.
  * Column headers only on row 1.
  * Decimal separator must be a comma, so amounts are written as text ("12,34").

Multi-rate invoices become one accounting line per VAT rate, each with its own
net, tax and account code, all sharing the same entry number.
"""

from collections.abc import Mapping
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from invoice_core.amounts import normalize_tax_lines, parse_amount

MOVEMENT_TYPE_INCOME = "0"   # Κωδικός είδους Κίνησης: 0 = Έσοδο
MOVEMENT_TYPE_EXPENSE = "1"  #                         1 = Έξοδο
DEFAULT_VAT_REGIME = "2"     # Καθεστώς ΦΠΑ: 2 = Κανονικό

ENTRIES_HEADERS = [
    "Κωδικός", "Κωδικός είδους Κίνησης", "ΚΕΠΥΟ", "Υποχρέωση Υποβολής",
    "Κωδικός Συναλλασσομένου", "ΑΦΜ Συναλλασσομένου", "Ημερομηνία", "Παραστατικό",
    "Αιτιολογία", "Ακυρωμένο", "Λογαριασμός", "Ποσοστό ΦΠΑ",
    "Καθαρή αξία Εσόδων/Εξόδων", "Φόρος (ΦΠΑ) εγγραφής",
]
ACCOUNTS_HEADERS = ["Λογαριασμός", "Περιγραφή", "Κατηγορία ΦΠΑ"]
PARTIES_HEADERS = ["Κωδικός Συναλλασσομένου", "Επωνυμία", "ΑΦΜ", "Καθεστώς ΦΠΑ"]
CLIENT_HEADERS = ["Επωνυμία Πελάτη", "ΑΦΜ Πελάτη"]


def money(value) -> str:
    d = value if isinstance(value, Decimal) else parse_amount(value)
    return "" if d is None else f"{d:.2f}".replace(".", ",")


def _first(values):
    if isinstance(values, (list, tuple)):
        return values[0] if values else ""
    return values or ""


def movement_type(invoice) -> str:
    return MOVEMENT_TYPE_INCOME if (invoice.direction or "expense") == "income" else MOVEMENT_TYPE_EXPENSE


def build_import_workbook(
    invoices: list,
    chart: Mapping[str, tuple],
    client_name: str | None = None,
    client_vat: str | None = None,
) -> bytes:
    """`chart` maps account code → (Greek name, VAT category)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Εγγραφές-Άρθρα"
    ws.append(ENTRIES_HEADERS)

    party_codes, party_names, used_accounts = {}, {}, set()

    for entry_no, inv in enumerate(invoices, start=1):
        vat = (inv.counterparty_vat or _first(inv.vat_numbers) or "").strip()
        if vat and vat not in party_codes:
            party_codes[vat] = len(party_codes) + 1
            party_names[vat] = _first(inv.company_names)

        lines = normalize_tax_lines(
            inv.vat_categories_analysis,
            subtotal=inv.subtotal_amount,
            vat_total=inv.vat_amount,
            default_account=inv.account_code or "",
        )
        for ln in lines:
            account = ln.get("account_code") or inv.account_code or ""
            if account:
                used_accounts.add(account)
            ws.append([
                entry_no, movement_type(inv), "1", "0",
                party_codes.get(vat, ""), vat, inv.invoice_date or "",
                _first(inv.invoice_numbers),
                _first(inv.line_items_generic_category) or (inv.invoice_type or ""),
                "0", account,
                "" if ln.get("rate") in (None, "") else ln.get("rate"),  # keep 0% (exempt) as 0
                money(ln.get("net")), money(ln.get("tax")),
            ])

    ws_acc = wb.create_sheet("Λογαριασμοί")
    ws_acc.append(ACCOUNTS_HEADERS)
    for code in sorted(used_accounts):
        if code in chart:
            name, vat_category = chart[code]
            ws_acc.append([code, name, vat_category or ""])

    ws_p = wb.create_sheet("Συναλλασσόμενοι")
    ws_p.append(PARTIES_HEADERS)
    for vat, code in party_codes.items():
        ws_p.append([code, party_names.get(vat, ""), vat, DEFAULT_VAT_REGIME])

    ws_c = wb.create_sheet("Πελάτης")
    ws_c.append(CLIENT_HEADERS)
    if client_name or client_vat:
        ws_c.append([client_name or "", client_vat or ""])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
