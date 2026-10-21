"""Money parsing and formatting.

All money is handled with :class:`decimal.Decimal`; floats are never used.
Display format uses a comma (``15,00 BYN``), exports use a dot.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum

MAX_DECIMALS = 2


class AmountErrorCode(StrEnum):
    NOT_A_NUMBER = "not_a_number"
    NOT_FINITE = "not_finite"
    BELOW_MIN = "below_min"
    NON_POSITIVE = "non_positive"
    ABOVE_MAX = "above_max"
    TOO_MANY_DECIMALS = "too_many_decimals"


class AmountError(ValueError):
    def __init__(self, code: AmountErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_amount(raw: str, *, min_amount: Decimal, max_amount: Decimal) -> Decimal:
    """Parse a user-entered BYN amount.

    Accepts a dot or a comma as the decimal separator and trims spaces.
    Rejects NaN/Infinity, non-positive values, values with more than two
    decimal places, values below ``min_amount`` and above ``max_amount``.
    """
    cleaned = raw.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not cleaned:
        raise AmountError(AmountErrorCode.NOT_A_NUMBER, "amount is empty")

    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise AmountError(AmountErrorCode.NOT_A_NUMBER, "amount is not a number") from exc

    if not amount.is_finite():
        raise AmountError(AmountErrorCode.NOT_FINITE, "amount must be finite")

    if amount.as_tuple().exponent < -MAX_DECIMALS:
        raise AmountError(
            AmountErrorCode.TOO_MANY_DECIMALS,
            f"amount must have at most {MAX_DECIMALS} decimal places",
        )

    if amount <= 0:
        raise AmountError(AmountErrorCode.NON_POSITIVE, "amount must be positive")

    if amount < min_amount:
        raise AmountError(AmountErrorCode.BELOW_MIN, "amount is below the minimum")

    if amount > max_amount:
        raise AmountError(AmountErrorCode.ABOVE_MAX, "amount exceeds the maximum")

    return amount.quantize(Decimal("0.01"))


def format_money(amount: Decimal | int | str) -> str:
    """Format money for Russian UI: ``15,00``."""
    value = Decimal(amount).quantize(Decimal("0.01"))
    return f"{value:.2f}".replace(".", ",")


def format_money_byn(amount: Decimal | int | str) -> str:
    return f"{format_money(amount)} BYN"


def format_money_export(amount: Decimal | int | str) -> str:
    """Format money for CSV/XLSX: ``15.00``."""
    return f"{Decimal(amount).quantize(Decimal('0.01')):.2f}"


def decimal_or_zero(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal("0.00")
