from datetime import date
from decimal import Decimal

import pytest

from invoice_core.amounts import normalize_invoice_date, normalize_tax_lines, parse_amount, parse_date, sum_tax


@pytest.mark.parametrize("raw,expected", [
    ("1.234,56 €", Decimal("1234.56")),
    ("1234.56", Decimal("1234.56")),
    ("12,40", Decimal("12.40")),
    (24, Decimal("24")),
    ("n/a", None),
    (None, None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("25/12/2026", date(2026, 12, 25)),
    ("2026-12-25", date(2026, 12, 25)),
    ("September 3, 2026", date(2026, 9, 3)),
    ("25.12.2026", date(2026, 12, 25)),
    ("not a date", None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_unparseable_date_is_kept_for_human_review():
    assert normalize_invoice_date("Δεκέμβριος") == "Δεκέμβριος"
    assert normalize_invoice_date("2026-01-05") == "05/01/2026"


def test_tax_lines_from_structured_multi_rate_answer():
    lines = normalize_tax_lines(
        [{"rate": 24, "net": 100, "tax": 24, "mydata_category": "category2_4"},
         {"rate": "13%", "net": "10,00", "tax": "1,30"}],
        default_account="64.08",
    )
    assert [(ln["rate"], ln["net"], ln["tax"]) for ln in lines] == [("24", "100.00", "24.00"), ("13", "10.00", "1.30")]
    assert lines[0]["mydata_category"] == "category2_4"
    assert all(ln["account_code"] == "64.08" for ln in lines)
    assert sum_tax(lines) == Decimal("25.30")


def test_tax_lines_derive_net_from_rate_when_missing():
    (line,) = normalize_tax_lines({"24%": "3,60"})
    assert line == {"rate": "24", "net": "15.00", "tax": "3.60", "account_code": ""}


def test_zero_rate_is_a_real_rate():
    (line,) = normalize_tax_lines([{"rate": 0, "net": "50", "tax": "0"}])
    assert line["rate"] == "0"


def test_fallback_to_totals_when_model_gives_no_breakdown():
    (line,) = normalize_tax_lines(None, subtotal="100,00", vat_total="24,00", default_account="64.98")
    assert (line["net"], line["tax"], line["account_code"]) == ("100.00", "24.00", "64.98")
