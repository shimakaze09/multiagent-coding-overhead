# ledgerly conventions

These rules are relied on by the finance team's other tools. Changes to them
need a changelog entry and a major version.

## Amounts and rounding

* Money is a `Decimal` amount plus an ISO 4217 currency code. Floats are rejected.
* Arithmetic keeps full precision. Rounding happens only when explicitly asked
  (`Money.rounded()`), when a value is allocated, stored or reported.
* Rounding is to the currency's minor unit (USD, EUR, GBP, CHF: 2; JPY: 0;
  KWD: 3), always `ROUND_HALF_EVEN` (banker's rounding).

## Allocation

`Money.allocate(ratios)` splits the rounded amount into whole minor units:

1. every part gets the floor of its exact proportional share;
2. the minor units left over are handed out one at a time to the parts with
   the largest fractional remainder; on a tie the earlier part wins;
3. a negative amount is split like its absolute value, then negated;
4. a zero ratio always receives exactly zero.

The parts always sum exactly to the rounded amount.

## Journal lines

A line has a signed amount: positive = debit, negative = credit. An entry must
net to exactly zero in every currency it uses. `Line.debit` and `Line.credit`
return the positive side amount or `None`.

## Exchange rates

* A rate for `(base, quote)` applies from its effective date until the next
  rate for the same pair. Dates before the first rate have no rate.
* Adding a rate for a pair and date that already has one replaces it.
* Providers publish corrections at any time; a rate table must give the same
  answers whatever order its rates were added in.
* A missing pair uses the inverse of the opposite pair, else triangulates
  through USD.
* `Ledger.balance_in` converts each line at the rate effective on its entry's
  date and rounds the total once.

## Files

`store.save` writes the current schema. `store.load` must keep reading every
schema listed in `store.READABLE_SCHEMAS`. Rates are written oldest first per
pair.
