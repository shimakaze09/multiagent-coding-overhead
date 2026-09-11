"""Reports in the ledger's reporting currency."""

from .money import Money


def format_money(money):
    """'1,234.50 EUR': grouped thousands, two decimals."""
    m = money.rounded()
    return f"{m.amount:,.2f} {m.currency}"


def trial_balance(ledger, as_of=None):
    cur = ledger.reporting_currency
    rows = []
    total_debit = total_credit = Money.zero(cur)
    for account in ledger.chart:
        bal = ledger.balance_in(account.code, as_of)
        if bal.is_zero():
            continue
        debit = bal if bal.amount > 0 else Money.zero(cur)
        credit = -bal if bal.amount < 0 else Money.zero(cur)
        rows.append({"code": account.code, "name": account.name, "debit": debit, "credit": credit})
        total_debit += debit
        total_credit += credit
    return {"currency": cur, "rows": rows, "total_debit": total_debit, "total_credit": total_credit}


def income_statement(ledger, start, end):
    """Income and expenses per account for entries dated start..end inclusive.
    Both are shown as positive numbers; net income = income - expenses."""
    cur = ledger.reporting_currency
    income, expenses = [], []
    for account in ledger.chart:
        if account.type not in ("income", "expense"):
            continue
        total = Money.zero(cur)
        for entry, line in ledger.lines_for(account.code, end):
            if entry.on >= start:
                total += ledger.convert_line(entry, line, cur)
        total = total.rounded()
        if total.is_zero():
            continue
        if account.type == "income":
            income.append({"code": account.code, "name": account.name, "amount": -total})
        else:
            expenses.append({"code": account.code, "name": account.name, "amount": total})
    total_income = sum((r["amount"] for r in income), Money.zero(cur))
    total_expenses = sum((r["amount"] for r in expenses), Money.zero(cur))
    return {"currency": cur, "income": income, "expenses": expenses,
            "net_income": total_income - total_expenses}


def render_trial_balance(tb):
    lines = [f"{'code':<8}{'account':<24}{'debit':>16}{'credit':>16}"]
    for r in tb["rows"]:
        lines.append(f"{r['code']:<8}{r['name']:<24}{format_money(r['debit']):>16}"
                     f"{format_money(r['credit']):>16}")
    lines.append(f"{'':<8}{'total':<24}{format_money(tb['total_debit']):>16}"
                 f"{format_money(tb['total_credit']):>16}")
    return "\n".join(lines)
