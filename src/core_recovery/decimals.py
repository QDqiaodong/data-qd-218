"""Exact decimal helpers used by the offline recovery archive.

The legacy :func:`core_recovery.process` API keeps its existing ``Fraction``
behaviour.  The newer archive interfaces accept only finite decimal strings so
that callers cannot accidentally introduce binary-float placement errors.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Optional

_DECIMAL_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z"
)


def parse_decimal(value: object, field: str = "decimal value") -> Fraction:
    """Parse a finite decimal string into an exact :class:`Fraction`."""

    if not isinstance(value, str):
        raise ValueError(f"{field} must be a decimal string")
    if len(value) > 4096 or not _DECIMAL_RE.match(value):
        raise ValueError(f"{field} must be a finite decimal string")
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a finite decimal string") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{field} must be finite")
    return Fraction(decimal_value)


def parse_nonnegative(value: object, field: str) -> Fraction:
    result = parse_decimal(value, field)
    if result < 0:
        raise ValueError(f"{field} must be nonnegative")
    return result


def decimal_to_string(value: Fraction) -> str:
    """Render an exact decimal rational in stable plain decimal notation.

    Input decimals have denominators containing only factors 2 and 5.  Additions
    and subtractions preserve that property, so every geometry result can be
    rendered as a finite decimal.
    """

    if value == 0:
        return "0"

    sign = "-" if value < 0 else ""
    numerator = abs(value.numerator)
    denominator = value.denominator

    powers_of_two = 0
    while denominator % 2 == 0:
        denominator //= 2
        powers_of_two += 1

    powers_of_five = 0
    while denominator % 5 == 0:
        denominator //= 5
        powers_of_five += 1

    if denominator != 1:  # Defensive: this cannot occur for decimal linear sums.
        return f"{sign}{value.numerator}/{value.denominator}"

    scale = max(powers_of_two, powers_of_five)
    numerator *= 2 ** (scale - powers_of_two)
    numerator *= 5 ** (scale - powers_of_five)

    if scale == 0:
        return f"{sign}{numerator}"

    digits = str(numerator).zfill(scale + 1)
    integer_part = digits[:-scale]
    fractional_part = digits[-scale:].rstrip("0")
    if fractional_part:
        return f"{sign}{integer_part}.{fractional_part}"
    return f"{sign}{integer_part}"


def canonical_decimal(value: object, field: str = "decimal value") -> str:
    return decimal_to_string(parse_decimal(value, field))


def optional_decimal(
    mapping: dict,
    names: tuple[str, ...],
    field: str,
    *,
    nonnegative: bool = False,
) -> Optional[Fraction]:
    for name in names:
        if name in mapping and mapping[name] is not None:
            if nonnegative:
                return parse_nonnegative(mapping[name], field)
            return parse_decimal(mapping[name], field)
    return None
