from decimal import Decimal

import pytest

from bot.domain.money import (
    AmountError,
    AmountErrorCode,
    format_money,
    format_money_byn,
    format_money_export,
    parse_amount,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5", Decimal("5.00")),
        ("5.5", Decimal("5.50")),
        (" 9,99 ", Decimal("9.99")),
        ("1 000,50", Decimal("1000.50")),
        ("10.00", Decimal("10.00")),
    ],
)
def test_parse_amount_accepts_comma_and_dot(raw, expected):
    assert parse_amount(raw, min_amount=Decimal("5"), max_amount=Decimal("10000")) == expected


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("", AmountErrorCode.NOT_A_NUMBER),
        ("abc", AmountErrorCode.NOT_A_NUMBER),
        ("NaN", AmountErrorCode.NOT_FINITE),
        ("Infinity", AmountErrorCode.NOT_FINITE),
        ("-5", AmountErrorCode.NON_POSITIVE),
        ("0", AmountErrorCode.NON_POSITIVE),
        ("4.99", AmountErrorCode.BELOW_MIN),
        ("10000.01", AmountErrorCode.ABOVE_MAX),
        ("5.001", AmountErrorCode.TOO_MANY_DECIMALS),
    ],
)
def test_parse_amount_rejects(raw, code):
    with pytest.raises(AmountError) as error:
        parse_amount(raw, min_amount=Decimal("5"), max_amount=Decimal("10000"))
    assert error.value.code is code


def test_minimum_is_inclusive():
    assert parse_amount("5.00", min_amount=Decimal("5"), max_amount=Decimal("10")) == Decimal(
        "5.00"
    )


def test_money_formatting():
    assert format_money(Decimal("15.00")) == "15"
    assert format_money("9.9") == "9,9"
    assert format_money("9.05") == "9,05"
    assert format_money_byn(Decimal("5")) == "5 BYN"
    assert format_money_export(Decimal("15.5")) == "15.50"
