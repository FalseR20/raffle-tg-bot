from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bot.texts import privacy, ru
from bot.texts.markdown import MESSAGE_LIMIT, safe_markdown, validate_markdown_v2

TZ = ZoneInfo("Europe/Minsk")
HOSTILE = 'Org "A" [x](y). ! _*~`'
NOW = datetime(2026, 10, 21, 14, 35, tzinfo=UTC)


def raffle():
    return SimpleNamespace(
        title=HOSTILE,
        description="Описание с точкой. И тире - вот.",
        ticket_price=Decimal("5.00"),
        min_amount=Decimal("5.00"),
        prizes_count=3,
        end_at=NOW,
        start_at=NOW,
        channel_title='Канал "Тест" [x]',
        channel_url="https://t.me/channel_a",
    )


def _privacy_facts(**overrides):
    values = {
        "operator_name": "Иван_Петров [тест] (тест).",
        "operator_address": "г. Минск, ул. Примерная, д. 1",
        "storage_country": "Беларусь",
        "support_contact": "@support",
        "rules_url": "https://example.org/rules?x=1&y=2",
        "privacy_url": "https://example.org/privacy",
        "rules_version": "1.0",
        "privacy_version": "2.0",
        "receipt_retention_days": 90,
        "require_age_confirmation": True,
        "channel_title": 'Канал "Тест"',
    }
    values.update(overrides)
    return SimpleNamespace(**values)


TEXTS = [
    privacy.processing_notice(_privacy_facts()),
    privacy.rights_notice(_privacy_facts()),
    privacy.operator_missing_notice(),
    privacy.withdrawal_prompt(),
    privacy.withdrawal_done(failed=0),
    privacy.withdrawal_done(failed=2),
    privacy.withdrawal_done(failed=0, profile_cleared=False),
    privacy.withdrawal_kept(),
    *privacy.policy_pages(_privacy_facts()),
    *privacy.policy_pages(
        _privacy_facts(storage_country="Германия", operator_name=None, operator_address=None)
    ),
    *privacy.policy_pages(_privacy_facts(storage_country=None)),
    ru.no_active_raffle_text('Канал "Тест"'),
    ru.scheduled_raffle_text(raffle(), TZ),
    ru.active_raffle_text(raffle(), TZ, has_consent=True),
    ru.active_raffle_text(raffle(), TZ, has_consent=False),
    ru.payment_methods_text(HOSTILE),
    ru.payment_instructions_text(
        organization_name=HOSTILE,
        payment_method_title="Карта. BYN",
        payment_instructions="Переведите:\n`1234 5678`\n\n*Получатель:* Иван",
        payment_code="K7M2Q",
        min_amount=Decimal("5"),
        ticket_price=Decimal("5"),
    ),
    *[
        ru.invalid_amount_text(code, min_amount=Decimal("5"), max_amount=Decimal("10000"))
        for code in (
            "below_min",
            "above_max",
            "too_many_decimals",
            "not_a_number",
            "non_positive",
            "unknown",
        )
    ],
    ru.amount_preview_text(
        amount=Decimal("9"),
        current_amount=Decimal("9"),
        preview_amount=Decimal("18"),
        preview_tickets=3,
        preview_delta=2,
    ),
    ru.invalid_file_text(),
    ru.file_too_large_text(20),
    ru.pending_review_text(
        public_id="A-2026-000123",
        organization_name=HOSTILE,
        payment_method_title="Карта. BYN",
        amount=Decimal("6"),
    ),
    ru.admin_card_text(
        public_id="A-2026-000123",
        payment_code="K7M2Q",
        user_mention=ru.esc("@user_name (Иван)"),
        organization_name=HOSTILE,
        payment_method_title="Card. BYN",
        amount=Decimal("6"),
        current_amount=Decimal("9"),
        preview_delta=2,
        preview_tickets=3,
        submitted_at=NOW,
        tz=TZ,
        raffle_title=HOSTILE,
    ),
    ru.confirmed_card_text(
        public_id="A-2026-000123",
        reviewer='Иван "И" (x)',
        confirmed_at=NOW,
        tz=TZ,
        ranges="2–3",
        total_tickets=3,
    ),
    ru.rejected_card_text(
        public_id="A-2026-000123",
        reason="чек. не читается!",
        reviewer="Иван",
        rejected_at=NOW,
        tz=TZ,
    ),
    ru.user_confirmation_text(
        public_id="A-2026-000123",
        amount=Decimal("6"),
        organization_name=HOSTILE,
        total_amount=Decimal("15"),
        new_numbers="2–3",
        total_tickets=3,
        all_numbers="1, 2–3",
        channel_title='Канал "Тест"',
        channel_url="https://t.me/channel_a",
    ),
    ru.rejection_text(
        public_id="A-2026-000123",
        reason="платёж (не) найден.",
        support_contact="@support.help",
    ),
    ru.revision_request_text("A-2026-000123"),
    ru.closed_text('Канал "Тест"'),
    ru.no_access_text(),
    ru.use_dm_text(),
    ru.my_summary_text(
        raffle_title=HOSTILE,
        confirmed_amount=Decimal("15"),
        tickets=3,
        ranges="1, 2–3",
    ),
    ru.results_text(
        raffle_title=HOSTILE,
        confirmed_amount=Decimal("15"),
        tickets=3,
        ranges="1, 2–3",
        prizes_count=3,
        last_ticket_number=10,
        winners=[(1, 3), (2, 5)],
        user_winning_places=[(1, 3)],
    ),
    ru.draft_raffle_delete_prompt(HOSTILE, applications=0),
    ru.draft_raffle_delete_prompt(HOSTILE, applications=2),
    ru.archived_raffle_delete_prompt(HOSTILE, applications=1, winners=5),
    ru.application_line(
        public_id="A-2026-000123",
        organization=HOSTILE,
        method="Карта. BYN",
        amount=Decimal("9"),
        status="на проверке.",
        created_at=NOW,
        tz=TZ,
    ),
]


@pytest.mark.parametrize("text", TEXTS)
def test_all_templates_are_valid_markdown(text):
    validate_markdown_v2(text)


def test_safe_markdown_escapes_legacy_invalid_instructions():
    invalid = "Там сям (номер) 1234"
    assert safe_markdown(invalid) == "Там сям \\(номер\\) 1234"
    validate_markdown_v2(
        ru.payment_instructions_text(
            organization_name="Org",
            payment_method_title="Карта",
            payment_instructions=invalid,
            payment_code="K7M2Q",
            min_amount=Decimal("5"),
            ticket_price=Decimal("5"),
        )
    )
    valid = "Там сям по номеру `1234`"
    assert safe_markdown(valid) == valid


def test_all_admin_wizard_prompts_are_valid_markdown():
    from bot.handlers.admin.raffle_wizard import _PROMPTS

    for prompt in _PROMPTS.values():
        validate_markdown_v2(prompt)


def test_admin_wizard_default_buttons_show_values():
    from bot.handlers.admin.raffle_wizard import (
        _channel_title_default_text,
        _channel_url_default_text,
        _min_default_text,
    )

    settings = SimpleNamespace(
        default_min_amount=Decimal("10"),
        channel_url="https://t.me/charity",
        channel_title="Благотворительный канал",
    )
    assert _min_default_text(settings, Decimal("5")) == "✅ 10 BYN"
    assert _min_default_text(settings, Decimal("15")) == "✅ 15 BYN"
    assert _channel_url_default_text(settings) == "✅ https://t.me/charity"
    assert _channel_title_default_text(settings) == "✅ Благотворительный канал"


def test_draft_delete_prompt_mentions_applications_when_present():
    empty = ru.draft_raffle_delete_prompt("Весна", applications=0)
    few = ru.draft_raffle_delete_prompt("Весна", applications=2)
    assert "Заявок в этом черновике нет" in empty
    assert "2 заявки" in few
    assert "файлы чеков" in few
    validate_markdown_v2(empty)
    validate_markdown_v2(few)


def test_archived_delete_prompt_uses_russian_plurals():
    one = ru.archived_raffle_delete_prompt("Весна", applications=1, winners=1)
    few = ru.archived_raffle_delete_prompt("Весна", applications=2, winners=3)
    many = ru.archived_raffle_delete_prompt("Весна", applications=5, winners=11)
    assert "1 заявка" in one
    assert "1 победитель" in one
    assert "2 заявки" in few
    assert "3 победителя" in few
    assert "5 заявок" in many
    assert "11 победителей" in many
    validate_markdown_v2(one)
    validate_markdown_v2(few)
    validate_markdown_v2(many)


def test_privacy_policy_states_what_the_raffle_stores():
    facts = _privacy_facts()
    notice = privacy.processing_notice(facts)
    rights = privacy.rights_notice(facts)
    policy = "\n".join(privacy.policy_pages(facts))
    for page in privacy.policy_pages(facts):
        assert len(page) < MESSAGE_LIMIT
    assert "Оператор: Иван\\_Петров" in notice
    assert "ник" in notice
    assert "скриншот" in notice
    assert "Telegram" in notice
    assert "примите правила розыгрыша" in rights
    assert "Правила розыгрыша" in rights
    assert "отозвать" in rights.lower() or "Отозвать" in rights or "отозвать" in rights
    assert "99\\-З" in policy
    assert "чек" in policy
    assert "Беларуси" in policy
    closed = privacy.operator_missing_notice()
    assert "закрыто" in closed
    foreign = "\n".join(privacy.policy_pages(_privacy_facts(storage_country="Германия")))
    assert "Германия" in foreign
    unknown = "\n".join(privacy.policy_pages(_privacy_facts(storage_country=None)))
    assert "не указана" in unknown


def test_ranges_and_pluralization_in_texts():
    assert "1, 2–3" in ru.my_summary_text(
        raffle_title="X",
        confirmed_amount=Decimal("15"),
        tickets=3,
        ranges="1, 2–3",
    )
    assert "2 номера" in ru.amount_preview_text(
        amount=Decimal("9"),
        current_amount=Decimal("9"),
        preview_amount=Decimal("18"),
        preview_tickets=3,
        preview_delta=2,
    )
