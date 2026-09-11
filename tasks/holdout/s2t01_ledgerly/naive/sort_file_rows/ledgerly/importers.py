"""Import bank statements and rate files."""

import csv
import io
import re
from datetime import date, datetime

from .currency import minor_units
from .fx import RateTable
from .journal import Entry, Line
from .money import Money, to_decimal

_GROUPED = re.compile(r"\d{1,3}(,\d{3})+(\.\d+)?")
_PLAIN = re.compile(r"\d+(\.\d+)?")


def parse_amount(text, currency):
    """'1,234.56' -> Decimal('1234.56'). '(12.00)' and '-12.00' are negative.
    More decimals than the currency's minor unit are rejected."""
    t = text.strip().replace(" ", "")
    negative = False
    if t.startswith("(") and t.endswith(")"):
        negative, t = True, t[1:-1]
    if t.startswith("-"):
        negative, t = not negative, t[1:]
    if not (_GROUPED.fullmatch(t) or _PLAIN.fullmatch(t)):
        raise ValueError(f"not an amount: {text!r}")
    digits = t.replace(",", "")
    decimals = len(digits.partition(".")[2])
    if decimals > minor_units(currency):
        raise ValueError(f"{text!r} has more decimals than {currency} allows")
    value = to_decimal(digits)
    return -value if negative else value


def parse_date(text, dayfirst=False):
    text = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    return datetime.strptime(text, "%d/%m/%Y" if dayfirst else "%m/%d/%Y").date()


def parse_bank_csv(text, *, bank_account, contra_account, currency, dayfirst=False,
                   id_prefix="BANK"):
    """Columns: date, description, amount. Positive amounts are money in, which
    debits the bank account and credits the contra account."""
    entries = []
    for n, row in enumerate(csv.DictReader(io.StringIO(text)), start=1):
        amount = parse_amount(row["amount"], currency)
        if amount == 0:
            continue
        money = Money(amount, currency)
        entries.append(Entry(f"{id_prefix}-{n}", parse_date(row["date"], dayfirst),
                             [Line(bank_account, money), Line(contra_account, -money)],
                             row.get("description", "").strip()))
    return entries


def parse_rates_csv(text):
    """Columns: date, base, quote, rate (ISO dates), in the provider's order."""
    table = RateTable()
    rows = sorted(csv.DictReader(io.StringIO(text)), key=lambda r: r["date"].strip())
    for row in rows:
        table.add(date.fromisoformat(row["date"].strip()), row["base"].strip(),
                  row["quote"].strip(), to_decimal(row["rate"].strip()))
    return table
