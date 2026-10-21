import pytest

from bot.texts.markdown import (
    MarkdownError,
    escape_markdown_v2,
    is_valid_markdown_v2,
    to_plain_text,
    validate_markdown_v2,
)
from bot.texts.ru import admin_card_text


def test_escape_covers_every_special_character():
    raw = "_*[]()~`>#+-=|{}.!\\"
    escaped = escape_markdown_v2(raw)
    assert escaped == "".join(f"\\{char}" for char in raw)
    validate_markdown_v2(escaped)


def test_escape_dynamic_user_name_prevents_markup_breakage():
    name = "*Иван* _Петров_ [x](y)"
    result = f"Имя: {escape_markdown_v2(name)}"
    validate_markdown_v2(result)
    assert to_plain_text(result) == "Имя: Иван Петров [x](y)"


@pytest.mark.parametrize(
    "text",
    [
        "Просто текст без спецсимволов",
        "*Жирный* и _курсив_, __подчёркнутый__, ~зачёркнутый~",
        "Код: `1234 5678`",
        "```pre\\nformatted```",
        "[Ссылка](https://example.com/a\\.b)",
        "Экранированная точка\\. И тире \\- вот так\\.",
    ],
)
def test_valid_markdown_accepted(text):
    assert is_valid_markdown_v2(text)


@pytest.mark.parametrize(
    "text",
    [
        "Точка. Не экранирована",
        "*незакрытый",
        "`незакрытый код",
        "[текст](url",
        "[]()",
        "Скобка ) лишняя",
        "Слэш в конце \\",
    ],
)
def test_invalid_markdown_rejected(text):
    assert not is_valid_markdown_v2(text)
    with pytest.raises(MarkdownError):
        validate_markdown_v2(text)


def test_payment_instructions_allow_copyable_values_and_formatting():
    instructions = (
        "Переведите на карту:\n`1234 5678 9012 3456`\n\n"
        "*Получатель:* Иван Иванов\n*Назначение:* пожертвование"
    )
    validate_markdown_v2(instructions)


def test_to_plain_text_strips_markup_and_escapes():
    assert to_plain_text("*Жирный* и `код`\\. [сайт](https://x\\.y)") == (
        "Жирный и код. сайт (https://x.y)"
    )


def test_admin_card_is_valid_markdown_for_hostile_names():
    text = admin_card_text(
        public_id="A-2026-000123",
        payment_code="K7M2Q",
        user_mention=escape_markdown_v2("@user (Иван_*Петров*)"),
        organization_name='Org "A" [test]',
        payment_method_title="Card.BYN",
        amount=__import__("decimal").Decimal("6"),
        current_amount=__import__("decimal").Decimal("9"),
        preview_delta=2,
        preview_tickets=3,
        submitted_at=None,
        tz=__import__("zoneinfo").ZoneInfo("Europe/Minsk"),
        raffle_title="Осень 2026",
    )
    validate_markdown_v2(text)
