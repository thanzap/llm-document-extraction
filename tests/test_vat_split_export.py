import io
from datetime import date
from types import SimpleNamespace

from openpyxl import load_workbook
from pypdf import PdfReader, PdfWriter

from invoice_core import pdf_split, vat
from invoice_core.accounting_export import build_import_workbook

# ---- VAT period maths -------------------------------------------------------

def test_period_bounds():
    assert vat.period_bounds("2026-Q4") == (date(2026, 10, 1), date(2026, 12, 31))
    assert vat.period_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))


def test_deadline_skips_weekend():
    # period ends 31 Dec 2026 → last day of Jan 2027 is Sunday → Friday 29 Jan
    assert vat.deadline(date(2026, 12, 31)) == date(2027, 1, 29)


def test_shift_and_default_period():
    assert vat.shift("2026-Q1", -1) == "2025-Q4"
    assert vat.shift("2026-12", 1) == "2027-01"
    assert vat.default_period("monthly", today=date(2026, 11, 10)) == "2026-10"


def _invoice(direction, d, lines, reviewed=True):
    return SimpleNamespace(direction=direction, invoice_date=d, reviewed=reviewed,
                           vat_categories_analysis=lines, subtotal_amount=None, vat_amount=None)


def test_vat_preview_balances_output_against_input():
    invoices = [
        _invoice("income", "10/10/2026", [{"rate": 24, "net": 1000, "tax": 240}]),
        _invoice("expense", "11/10/2026", [{"rate": 24, "net": 200, "tax": 48},
                                            {"rate": 13, "net": 100, "tax": 13}], reviewed=False),
        _invoice("expense", "01/09/2026", [{"rate": 24, "net": 999, "tax": 999}]),  # other period
        _invoice("expense", None, []),
    ]
    p = vat.preview(invoices, "2026-10")
    assert (p["output_vat"], p["input_vat"], p["balance"]) == (240.0, 61.0, 179.0)
    assert p["counts"]["not_validated"] == 1 and p["counts"]["no_date"] == 1


# ---- PDF splitting (model answer validated before any split) ----------------

def _pdf(pages: int) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_valid_ranges_must_cover_every_page_exactly_once():
    ok = {"documents": [{"from": 1, "to": 2}, {"from": 4, "to": 4}], "skip": [3]}
    assert pdf_split._valid_ranges(ok, 4) == [(1, 2), (4, 4)]
    assert pdf_split._valid_ranges({"documents": [{"from": 1, "to": 2}]}, 4) is None  # pages missing
    assert pdf_split._valid_ranges({"documents": [{"from": 1, "to": 3}, {"from": 3, "to": 4}]}, 4) is None
    assert pdf_split._valid_ranges({"documents": [{"from": 0, "to": 9}]}, 4) is None
    assert pdf_split._valid_ranges({"documents": "garbage"}, 4) is None


def test_split_produces_one_pdf_per_document():
    parts = pdf_split.split(_pdf(5), [(1, 2), (3, 3), (4, 5)])
    assert [len(PdfReader(io.BytesIO(p)).pages) for p in parts] == [2, 1, 2]


def test_single_page_pdf_never_calls_the_model():
    def boom(*a, **k):
        raise AssertionError("model should not be called")
    assert pdf_split.find_documents(_pdf(1), boom) is None


# ---- Accounting export ------------------------------------------------------

def test_export_one_line_per_vat_rate_with_comma_decimals():
    inv = SimpleNamespace(
        direction="expense", counterparty_vat="EL094019245", vat_numbers=[], company_names=["TeleNet S.A."],
        invoice_date="03/09/2026", invoice_numbers=["Α 5999"], line_items_generic_category="telecom",
        invoice_type="Τιμολόγιο", account_code="64.08", subtotal_amount=None, vat_amount=None,
        vat_categories_analysis=[{"rate": 24, "net": 50, "tax": 12}, {"rate": 0, "net": 5, "tax": 0}],
    )
    data = build_import_workbook([inv], {"64.08": ("Έξοδα τηλεπικοινωνιών", "1")}, "Blue Sea", "123456789")
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Εγγραφές-Άρθρα", "Λογαριασμοί", "Συναλλασσόμενοι", "Πελάτης"]
    rows = list(wb["Εγγραφές-Άρθρα"].iter_rows(min_row=2, values_only=True))
    assert [(r[0], r[1], r[11], r[12], r[13]) for r in rows] == [(1, "1", "24", "50,00", "12,00"),
                                                                  (1, "1", "0", "5,00", "0,00")]
    parties = list(wb["Συναλλασσόμενοι"].iter_rows(min_row=2, values_only=True))
    assert parties == [(1, "TeleNet S.A.", "EL094019245", "2")]
