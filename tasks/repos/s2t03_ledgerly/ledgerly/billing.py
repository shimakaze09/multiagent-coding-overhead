"""Invoices whose revenue is split across several income accounts."""

from .journal import Entry, Line


def _line(account, amount, memo):
    if amount.amount > 0:
        return Line(account, debit=amount, memo=memo)
    return Line(account, credit=-amount, memo=memo)


def split_invoice(entry_id, on, total, receivable, shares, description=""):
    """``shares`` maps income account -> ratio. Debits ``receivable`` with the
    rounded total and credits each income account its allocated part."""
    accounts = list(shares)
    parts = total.allocate([shares[a] for a in accounts])
    lines = [_line(receivable, total.rounded(), description)]
    lines += [_line(a, -p, description) for a, p in zip(accounts, parts) if not p.is_zero()]
    return Entry(entry_id, on, lines, description)
