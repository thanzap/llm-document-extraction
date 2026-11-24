from types import SimpleNamespace

import pytest

from invoice_core.extraction import Account, DuplicateInvoice, Owner, extract, post_process

CHART = [
    Account("64.08", "Έξοδα τηλεπικοινωνιών", "expense"),
    Account("64.98", "Διάφορα έξοδα", "expense"),
    Account("73.00", "Έσοδα από παροχή υπηρεσιών", "income"),
]
OWNER = Owner("Blue Sea Consulting", vat_number="EL123456789", business_description="IT consulting")

RAW = {
    "company_names": ["Blue Sea Consulting", "TeleNet S.A."],
    "vat_numbers": ["123456789", "EL094019245"],
    "seller_vat": "EL094019245",
    "direction": "expense",
    "invoice_numbers": ["Α 5999"],
    "invoice_date": "September 3, 2026",
    "total_amount": "62,00",
    "subtotal_amount": "50,00",
    "vat_amount": "12,00",
    "line_items_generic_category": "mobile & internet",
    "account_code": "64.08",
    "vat_categories_analysis": [{"rate": 24, "net": 50, "tax": 12,
                                 "mydata_category": "category2_4", "e3_code": "E3_585_013"}],
    "mark": "400001234567890",
}


def test_happy_path():
    inv = post_process(RAW, OWNER, CHART)
    assert inv.direction == "expense"
    assert inv.vat_numbers == ["EL094019245"]
    assert inv.invoice_date == "03/09/2026"
    assert inv.account_code == "64.08"
    assert inv.tax_lines[0]["e3_code"] == "E3_585_013"
    assert not inv.warnings


def test_wrong_side_account_is_rejected_and_rules_apply():
    raw = {**RAW, "account_code": "73.00"}  # an income account on an expense
    inv = post_process(raw, OWNER, CHART, keyword_rules={"internet": "64.08", "net": "64.98"})
    assert inv.account_code == "64.08"  # longest matching keyword wins
    assert "rejected" in inv.warnings[0]


def test_invented_mydata_codes_fall_back_to_official_default():
    raw = {**RAW, "vat_categories_analysis": [{"rate": 24, "tax": 12, "mydata_category": "category9_9",
                                               "e3_code": "E3_000"}]}
    line = post_process(raw, OWNER, CHART).tax_lines[0]
    assert (line["mydata_category"], line["e3_code"]) == ("category2_4", "E3_585_016")


def test_sale_issued_by_client_becomes_income():
    raw = {**RAW, "seller_vat": "EL123456789", "account_code": "73.00",
           "vat_numbers": ["123456789", "EL555444333"]}
    inv = post_process(raw, OWNER, CHART)
    assert inv.direction == "income" and inv.account_code == "73.00"
    assert inv.tax_lines[0]["mydata_category"].startswith("category1_")


def test_duplicate_is_raised():
    existing = [SimpleNamespace(mark="400001234567890", counterparty_vat=None, invoice_numbers=[], invoice_date=None)]
    with pytest.raises(DuplicateInvoice):
        post_process(RAW, OWNER, CHART, existing_invoices=existing)


def test_extract_passes_chart_and_client_context_to_the_model():
    seen = {}

    def fake_model(data, mime, prompt):
        seen["prompt"] = prompt
        return RAW

    extract(b"%PDF", "application/pdf", OWNER, CHART, fake_model)
    assert "64.08 = Έξοδα τηλεπικοινωνιών [EXPENSE]" in seen["prompt"]
    assert "VAT number EL123456789" in seen["prompt"]
