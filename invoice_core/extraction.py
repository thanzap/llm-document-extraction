"""Invoice extraction: one multimodal Gemini call, then deterministic post-processing.

The model reads the image/PDF directly (no separate OCR step) and answers in
JSON. Everything after that call is plain Python that checks, corrects and
normalises the answer, so the books never depend on the model being right
about things the data can settle on its own.

    file bytes ──► Gemini (JSON) ──► post_process() ──► reviewed by an accountant
                                       │
                                       ├─ drop the client's own name/VAT
                                       ├─ expense vs income: VAT match beats model guess
                                       ├─ account code: must exist AND be on the right side
                                       ├─ myDATA codes: only official ones survive
                                       ├─ amounts/dates/VAT lines normalised
                                       └─ duplicate check (ΜΑΡΚ, or VAT+number+date)
"""

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from invoice_core import classification, dedup
from invoice_core.amounts import normalize_invoice_date, normalize_tax_lines, parse_amount
from invoice_core.identity import drop_client_identity, resolve_direction

SYSTEM_PROMPT = (
    "You read an invoice or receipt image/PDF and extract its details. Respond with JSON only: "
    "'invoice_type', 'company_names' [the OTHER party only, NOT the reader], 'vat_numbers' "
    "[the OTHER party's only], 'invoice_numbers', 'invoice_date', 'payment_terms', "
    "'total_amount', 'subtotal_amount', 'vat_amount', 'line_items_generic_category', "
    "'vat_categories_analysis': one object per tax rate like {\"rate\": 24, \"net\": 0.00, "
    "\"tax\": 0.00, \"mydata_category\": \"category2_4\", \"e3_code\": \"E3_585_016\"} with codes "
    "chosen STRICTLY from the provided lists; 'account_code' chosen STRICTLY from the provided "
    "chart of accounts, on the correct [EXPENSE]/[INCOME] side; 'seller_vat' (the issuer's VAT "
    "as printed); 'direction' ('expense' | 'income'); 'mark' (the 15-digit ΜΑΡΚ if printed)."
)


@dataclass
class Owner:
    """The client whose books the document is recorded in."""
    name: str
    vat_number: str | None = None
    business_description: str | None = None

    def context(self) -> str:
        parts = [self.name]
        if self.vat_number:
            parts.append(f"VAT number {self.vat_number}")
        if self.business_description:
            parts.append(f"business: {self.business_description}")
        return "; ".join(parts)


@dataclass
class Account:
    code: str
    name: str
    kind: str  # 'expense' | 'income'


@dataclass
class ExtractedInvoice:
    direction: str
    company_names: list
    vat_numbers: list
    invoice_numbers: list
    invoice_date: str
    mark: str | None
    account_code: str
    tax_lines: list
    total_amount: str | None
    net_amount: object | None
    warnings: list = field(default_factory=list)


class DuplicateInvoice(Exception):
    def __init__(self, existing, reason: str):
        super().__init__(reason)
        self.existing, self.reason = existing, reason


def build_prompt(chart: Iterable[Account], owner: Owner) -> str:
    accounts = "\n".join(f"{a.code} = {a.name} [{a.kind.upper()}]" for a in chart)
    return (
        "Extract the document's details.\n\n"
        f"Chart of accounts to choose 'account_code' from:\n{accounts}\n\n"
        f"myDATA classification code lists:\n{classification.prompt_text()}\n\n"
        f"The reader (whose books this is recorded in): {owner.context()}."
    )


def call_gemini(client, model: str, file_bytes: bytes, mime_type: str, prompt: str) -> dict:
    """One multimodal call that must answer with complete JSON."""
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            max_output_tokens=4096,  # thinking models spend output tokens; a low cap truncates JSON
        ),
    )
    finish = getattr((response.candidates or [None])[0], "finish_reason", None)
    if finish and str(finish).endswith("MAX_TOKENS"):
        raise ValueError("model hit the token limit before finishing the JSON")
    text = (response.text or "").strip()
    if not text:
        raise ValueError("model returned an empty response")
    return json.loads(text)


def default_account(direction: str, category_text: str, rules: Mapping[str, str]) -> str:
    """Keyword rules (longest keyword first) for expenses; income goes to the
    default revenue account, since the same word ('food') means opposite things
    when bought vs sold."""
    if direction == "income":
        return "73.98"
    text = (category_text or "").lower()
    for kw in sorted(rules, key=len, reverse=True):
        if kw.lower() in text:
            return rules[kw]
    return "64.98"


def post_process(
    raw: dict,
    owner: Owner,
    chart: Iterable[Account],
    existing_invoices: Iterable = (),
    keyword_rules: Mapping[str, str] = None,
) -> ExtractedInvoice:
    """Turn the model's raw JSON into a validated invoice, or raise DuplicateInvoice."""
    accounts = {a.code: a for a in chart}
    warnings = []

    direction = resolve_direction(owner.vat_number, raw)
    company_names, vat_numbers = drop_client_identity(
        owner.vat_number, owner.name, raw.get("company_names"), raw.get("vat_numbers")
    )
    mark = dedup.normalize_mark(raw.get("mark"))
    invoice_date = normalize_invoice_date(raw.get("invoice_date"))

    hit = dedup.find_duplicate(
        existing_invoices, mark=mark,
        counterparty_vat=vat_numbers[0] if vat_numbers else None,
        invoice_numbers=raw.get("invoice_numbers"), invoice_date=invoice_date,
    )
    if hit:
        raise DuplicateInvoice(*hit)

    category = raw.get("line_items_generic_category") or ""
    category_text = " ".join(category) if isinstance(category, list) else str(category)
    ai_code = str(raw.get("account_code") or "").strip()
    if ai_code in accounts and accounts[ai_code].kind == direction:
        account_code = ai_code
    else:
        if ai_code:
            warnings.append(f"model's account {ai_code!r} rejected (unknown or wrong side)")
        account_code = default_account(direction, category_text, keyword_rules or {})

    tax_lines = normalize_tax_lines(
        raw.get("vat_categories_analysis"),
        subtotal=raw.get("subtotal_amount"), vat_total=raw.get("vat_amount"),
        default_account=account_code,
    )
    for line in tax_lines:
        line["mydata_category"], line["e3_code"] = classification.clean(
            direction, line.get("mydata_category"), line.get("e3_code")
        )

    return ExtractedInvoice(
        direction=direction,
        company_names=company_names,
        vat_numbers=vat_numbers,
        invoice_numbers=list(raw.get("invoice_numbers") or []),
        invoice_date=invoice_date,
        mark=mark,
        account_code=account_code,
        tax_lines=tax_lines,
        total_amount=raw.get("total_amount"),
        net_amount=parse_amount(raw.get("subtotal_amount")),
        warnings=warnings,
    )


def extract(
    file_bytes: bytes,
    mime_type: str,
    owner: Owner,
    chart: list,
    ask: Callable[[bytes, str, str], dict],
    **kwargs,
) -> ExtractedInvoice:
    """End to end. `ask(file_bytes, mime_type, prompt)` wraps the model call,
    e.g. functools.partial(call_gemini, genai.Client(), "gemini-2.5-flash")."""
    raw = ask(file_bytes, mime_type, build_prompt(chart, owner))
    return post_process(raw, owner, chart, **kwargs)
