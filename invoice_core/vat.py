"""VAT preview per client and period: output VAT (sales) − input VAT (expenses),
from the invoices on file, plus the periodic VAT return (Φ2) deadline."""

import calendar
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from invoice_core.amounts import normalize_tax_lines, parse_amount, parse_date


def _quarter(d: date) -> int:
    return (d.month - 1) // 3 + 1


def period_bounds(period: str) -> tuple:
    """'2026-10' → Oct 2026; '2026-Q4' → Oct–Dec 2026. Returns (start, end)."""
    m = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        start = date(year, 3 * q - 2, 1)
        end_month = 3 * q
    else:
        m = re.fullmatch(r"(\d{4})-(\d{2})", period)
        if not m or not 1 <= int(m.group(2)) <= 12:
            raise ValueError("Period must look like 2026-10 or 2026-Q4")
        year, end_month = int(m.group(1)), int(m.group(2))
        start = date(year, end_month, 1)
    return start, date(year, end_month, calendar.monthrange(year, end_month)[1])


def deadline(period_end: date) -> date:
    """Φ2 is due by the last working day of the month after the period
    (weekends skipped; public holidays not accounted for)."""
    nxt = period_end + timedelta(days=1)
    d = date(nxt.year, nxt.month, calendar.monthrange(nxt.year, nxt.month)[1])
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _label(d: date, kind: str) -> str:
    return f"{d.year}-Q{_quarter(d)}" if kind == "quarterly" else f"{d.year}-{d.month:02d}"


def shift(period: str, step: int) -> str:
    start, _ = period_bounds(period)
    kind = "quarterly" if "Q" in period else "monthly"
    months = 3 if kind == "quarterly" else 1
    total = start.year * 12 + (start.month - 1) + step * months
    return _label(date(total // 12, total % 12 + 1, 1), kind)


def default_period(kind: str, today: date = None) -> str:
    """The period whose return is due next: the previous one if its deadline
    hasn't passed, otherwise the current (in-progress) one."""
    today = today or date.today()
    current = _label(today, kind)
    previous = shift(current, -1)
    return previous if deadline(period_bounds(previous)[1]) >= today else current


def preview(invoices: list, period: str, pending_files: int = 0) -> dict:
    start, end = period_bounds(period)
    out_rates, in_rates = defaultdict(lambda: [Decimal(0), Decimal(0)]), defaultdict(lambda: [Decimal(0), Decimal(0)])
    counts = {"sales": 0, "expenses": 0, "not_validated": 0, "no_date": 0}

    for inv in invoices:
        d = parse_date(inv.invoice_date)
        if d is None:
            counts["no_date"] += 1
            continue
        if not start <= d <= end:
            continue
        income = (inv.direction or "expense") == "income"
        counts["sales" if income else "expenses"] += 1
        if not inv.reviewed:
            counts["not_validated"] += 1
        bucket = out_rates if income else in_rates
        lines = normalize_tax_lines(
            inv.vat_categories_analysis, subtotal=inv.subtotal_amount, vat_total=inv.vat_amount,
        )
        if lines:
            for ln in lines:
                key = ln["rate"] or "?"
                bucket[key][0] += parse_amount(ln["net"]) or Decimal(0)
                bucket[key][1] += parse_amount(ln["tax"]) or Decimal(0)
        else:
            bucket["?"][0] += parse_amount(inv.subtotal_amount) or Decimal(0)
            bucket["?"][1] += parse_amount(inv.vat_amount) or Decimal(0)

    def table(rates):
        return [
            {"rate": r, "net": float(v[0]), "vat": float(v[1])}
            for r, v in sorted(rates.items(), key=lambda kv: -float(kv[0]) if kv[0] not in ("?", "") else 1)
        ]

    output_vat = sum((v[1] for v in out_rates.values()), Decimal(0))
    input_vat = sum((v[1] for v in in_rates.values()), Decimal(0))
    due = deadline(end)
    return {
        "period": period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "deadline": due.isoformat(),
        "days_left": (due - date.today()).days,
        "in_progress": end >= date.today(),
        "output_vat": float(output_vat),
        "input_vat": float(input_vat),
        "balance": float(output_vat - input_vat),  # > 0 payable, < 0 credit carried forward
        "sales_net": float(sum((v[0] for v in out_rates.values()), Decimal(0))),
        "expenses_net": float(sum((v[0] for v in in_rates.values()), Decimal(0))),
        "output_by_rate": table(out_rates),
        "input_by_rate": table(in_rates),
        "counts": {**counts, "still_reading": pending_files},
    }
