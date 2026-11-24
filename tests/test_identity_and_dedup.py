from types import SimpleNamespace

from invoice_core.dedup import content_hash, find_duplicate, normalize_mark, normalize_number
from invoice_core.identity import drop_client_identity, normalize_vat, resolve_direction


def test_vat_normalisation_ignores_country_prefix_and_punctuation():
    assert normalize_vat("EL 802-616-916") == normalize_vat("802616916") == "802616916"


def test_client_identity_is_removed_from_extracted_parties():
    names, vats = drop_client_identity(
        "EL123456789", "Ταβέρνα Ο Γιάννης Ο.Ε.",
        ["Ταβέρνα Ο Γιάννης ΟΕ", "ACME Supplies S.A."], ["123456789", "EL999888777"],
    )
    # company_key keeps only [a-z0-9], so Greek names collapse to "" and are kept
    # unless the VAT check catches them; the VAT filter is the strong guarantee.
    assert vats == ["EL999888777"]
    assert "ACME Supplies S.A." in names


def test_latin_company_name_match_is_dropped():
    names, _ = drop_client_identity(None, "Blue Sea B.V.", ["BLUE SEA BV", "Other Ltd"], [])
    assert names == ["Other Ltd"]


def test_vat_match_overrides_model_direction():
    # model says expense, but the issuer is the client itself → a sale
    assert resolve_direction("EL123456789", {"seller_vat": "123456789", "direction": "expense"}) == "income"
    assert resolve_direction("123456789", {"seller_vat": "999", "direction": "income"}) == "expense"


def test_model_direction_used_only_without_vat_evidence():
    assert resolve_direction(None, {"direction": "income"}) == "income"
    assert resolve_direction(None, {"direction": "something odd"}) == "expense"


def test_mark_and_number_normalisation():
    assert normalize_mark("ΜΑΡΚ: 400001234567890") == "400001234567890"
    assert normalize_mark("12") is None
    # Greek Α/Τ and Latin A/T are the same series
    assert normalize_number("ΤΔΑ 1256") == normalize_number("TΔA-1256")


def _inv(**kw):
    base = dict(mark=None, counterparty_vat=None, invoice_numbers=[], invoice_date=None)
    return SimpleNamespace(**{**base, **kw})


def test_duplicate_by_mark():
    existing = [_inv(mark="400001234567890")]
    hit = find_duplicate(existing, mark="400001234567890")
    assert hit and hit[1] == "same ΜΑΡΚ 400001234567890"


def test_duplicate_by_vat_number_and_date_across_alphabets():
    existing = [_inv(counterparty_vat="EL999888777", invoice_numbers=["Α 102"], invoice_date="05/01/2026")]
    assert find_duplicate(existing, counterparty_vat="999888777", invoice_numbers=["A-102"], invoice_date="05/01/2026")
    other = ["A-103"]
    assert not find_duplicate(existing, counterparty_vat="999888777", invoice_numbers=other, invoice_date="05/01/2026")
    assert not find_duplicate(existing, counterparty_vat="999888777", invoice_numbers=["A-102"], invoice_date=None)


def test_content_hash_is_stable():
    assert content_hash(b"pdf") == content_hash(b"pdf") != content_hash(b"pdf2")
