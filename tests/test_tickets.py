from decimal import Decimal

import pytest

from bot.domain.tickets import (
    assign_range,
    build_preview,
    calculate_tickets,
    compress_numbers,
    compress_ranges,
    format_ticket_numbers,
    suggest_amount,
)
from bot.texts.ru import numbers_label, plural_ru, tickets_label

PRICE = Decimal("5.00")


def test_calculate_tickets_floors():
    assert calculate_tickets(Decimal("9"), PRICE) == 1
    assert calculate_tickets(Decimal("10"), PRICE) == 2
    assert calculate_tickets(Decimal("0"), PRICE) == 0


def test_9_plus_6_equals_3_tickets():
    first = build_preview(Decimal("9"), current_amount=None, current_tickets=0, ticket_price=PRICE)
    assert first.preview_tickets == 1
    assert first.delta == 1

    second = build_preview(
        Decimal("6"),
        current_amount=Decimal("9"),
        current_tickets=1,
        ticket_price=PRICE,
    )
    assert second.preview_amount == Decimal("15")
    assert second.preview_tickets == 3
    assert second.delta == 2


def test_9_plus_5_equals_2_tickets():
    preview = build_preview(
        Decimal("5"), current_amount=Decimal("9"), current_tickets=1, ticket_price=PRICE
    )
    assert preview.preview_tickets == 2
    assert preview.delta == 1


def test_remainders_are_per_participant():
    user_a = build_preview(Decimal("9"), current_amount=None, current_tickets=0, ticket_price=PRICE)
    user_b = build_preview(Decimal("9"), current_amount=None, current_tickets=0, ticket_price=PRICE)
    assert user_a.preview_tickets + user_b.preview_tickets == 2


def test_suggest_amount_uses_remainder_but_respects_minimum():
    preview = build_preview(
        Decimal("9"), current_amount=None, current_tickets=0, ticket_price=PRICE
    )
    assert suggest_amount(preview, PRICE, Decimal("5")) == Decimal("10")
    preview = build_preview(
        Decimal("5"), current_amount=Decimal("9"), current_tickets=1, ticket_price=PRICE
    )
    assert suggest_amount(preview, PRICE, Decimal("5")) == Decimal("6")
    preview = build_preview(
        Decimal("5"), current_amount=Decimal("5"), current_tickets=1, ticket_price=PRICE
    )
    assert suggest_amount(preview, PRICE, Decimal("5")) is None


def test_assign_range_is_contiguous():
    assert assign_range(0, 1).__dict__ == {"first": 1, "last": 1}
    assert assign_range(2, 2).__dict__ == {"first": 3, "last": 4}
    with pytest.raises(ValueError):
        assign_range(0, 0)


def test_compress_numbers():
    assert compress_numbers([4, 1, 3, 2]) == "1–4"
    assert compress_numbers([1, 3, 4]) == "1, 3–4"
    assert compress_numbers([]) == ""
    assert compress_ranges([(1, 1), (3, 4)]) == "1, 3–4"
    assert format_ticket_numbers([(1, 1), (2, 2), (5, 6)]) == "1–2, 5–6"


def test_russian_pluralization():
    assert plural_ru(1, "билет", "билета", "билетов") == "билет"
    assert plural_ru(2, "билет", "билета", "билетов") == "билета"
    assert plural_ru(5, "билет", "билета", "билетов") == "билетов"
    assert plural_ru(11, "билет", "билета", "билетов") == "билетов"
    assert tickets_label(21) == "21 билет"
    assert numbers_label(2) == "2 номерка"
