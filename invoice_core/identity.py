"""Deterministic guardrails around the model's reading of *who is who*.

An invoice names two parties. The model is asked to return only the
counterparty and to say whether the document is an expense or a sale, but
those are exactly the calls an LLM gets wrong on a busy receipt. When the
data allows a deterministic answer (VAT numbers match), it wins over the model.
"""

import re


def normalize_vat(v: str | None) -> str:
    """'EL 802-616-916' and '802616916' compare equal: strip punctuation and
    any 2-letter ISO country prefix (EL, GR, NL, ...)."""
    s = re.sub(r"[^A-Za-z0-9]", "", v or "").upper()
    if len(s) > 2 and s[:2].isalpha():
        s = s[2:]
    return s


def company_key(name: str | None) -> str:
    """Loose company-name key: lowercase alphanumerics only ('O.E.', 'B.V' dropped)."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def drop_client_identity(owner_vat, owner_company, company_names, vat_numbers):
    """Remove the client's own VAT number and company name from the extracted
    lists, keeping only the counterparty (belt-and-braces on top of the prompt)."""
    company_names = list(company_names or [])
    vat_numbers = list(vat_numbers or [])

    own_vat = normalize_vat(owner_vat)
    if own_vat:
        vat_numbers = [v for v in vat_numbers if normalize_vat(v) != own_vat]

    own_co = company_key(owner_company)
    if own_co:
        company_names = [
            c for c in company_names
            if not (company_key(c) and (company_key(c) == own_co
                    or company_key(c) in own_co or own_co in company_key(c)))
        ]
    return company_names, vat_numbers


def resolve_direction(owner_vat: str | None, extracted: dict) -> str:
    """'income' if the client issued the document, else 'expense'.

    Deterministic when the issuer's VAT is known: it either is the client's own
    VAT (a sale they issued) or it isn't. Only otherwise fall back to the model's guess.
    """
    own = normalize_vat(owner_vat)
    seller = normalize_vat(extracted.get("seller_vat"))
    if own and seller:
        return "income" if own == seller else "expense"
    return "income" if extracted.get("direction") == "income" else "expense"
