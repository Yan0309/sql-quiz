"""Compare a result set to a question's expected result.

We grade the *result set*, not the SQL text:
  * column names are ignored (aliases don't matter), column count must match
  * values are normalised: 3 == 3.0 == Decimal('3.00'), dates -> ISO strings
  * row order matters only when the question says so (question.ordered)
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation


def _norm(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float, Decimal)):
        try:
            d = Decimal(str(v))
        except InvalidOperation:
            return str(v)
        if d == d.to_integral_value():
            return str(int(d))
        # 6 decimal places is plenty for this quiz; avoids float noise
        return format(d.quantize(Decimal("0.000001")).normalize(), "f")
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    return str(v)


def normalise_rows(rows) -> list[tuple]:
    return [tuple(_norm(v) for v in row) for row in rows]


def _sort_key(row):
    # None sorts first and never compares against str
    return tuple((v is not None, v or "") for v in row)


def grade(question, columns, rows, truncated: bool = False) -> tuple[bool, str]:
    """Return (correct, short explanation)."""
    expected = normalise_rows(question.expected_rows)
    got = normalise_rows(rows)
    exp_cols = len(question.expected_columns)

    if len(columns) != exp_cols:
        return False, f"Expected {exp_cols} column(s), got {len(columns)}."
    if truncated or len(got) != len(expected):
        n = f"more than {len(got)}" if truncated else str(len(got))
        return False, f"Expected {len(expected)} row(s), got {n}."
    if question.ordered:
        if got == expected:
            return True, "Matches the expected result, in the right order."
        if sorted(got, key=_sort_key) == sorted(expected, key=_sort_key):
            return False, "Right rows, wrong order — check your ORDER BY."
        return False, "Values differ from the expected result."
    if sorted(got, key=_sort_key) == sorted(expected, key=_sort_key):
        return True, "Matches the expected result (order ignored)."
    return False, "Values differ from the expected result."
