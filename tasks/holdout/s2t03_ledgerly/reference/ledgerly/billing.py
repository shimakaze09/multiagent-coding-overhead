"""Invoices whose revenue is split across several income accounts."""

from .journal import Entry, Line


def split_invoice(entry_id, on, total, receivable, shares, description=""):
    """``shares`` maps income account -> ratio. Debits ``receivable`` with the
    rounded total and credits each income account its allocated part."""
    accounts = list(shares)
    parts = total.allocate([shares[a] for a in accounts])
    lines = [Line(receivable, total.rounded(), description)]
    lines += [Line(a, -p, description) for a, p in zip(accounts, parts) if not p.is_zero()]
    return Entry(entry_id, on, lines, description)
