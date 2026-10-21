from __future__ import annotations

from decimal import Decimal

import pytest

from bot.db.repositories import settings as settings_repo
from bot.db.session import session_scope
from bot.services.errors import ValidationError
from bot.services.settings import (
    describe_setting,
    load_runtime_settings,
    parse_setting_value,
)


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("max_donation", "250,50", "250.50"),
        ("receipt_retention_days", "120", 120),
        ("confirm_two_step", "да", True),
        ("confirm_two_step", "no", False),
        ("require_age_confirmation", "1", True),
        ("timezone", "Europe/Moscow", "Europe/Moscow"),
        ("management_log_chat_id", "-100123", -100123),
        ("channel_url", "", None),
        ("channel_url", "https://t.me/x", "https://t.me/x"),
    ],
)
def test_parse_setting_values(key, raw, expected):
    assert parse_setting_value(key, raw) == expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("max_donation", "-1"),
        ("receipt_retention_days", "0"),
        ("confirm_two_step", "maybe"),
        ("timezone", "Mars/Olympus"),
        ("rules_url", "ftp://example.org"),
        ("management_log_chat_id", "abc"),
        ("unknown", "value"),
    ],
)
def test_invalid_setting_values(key, raw):
    with pytest.raises(ValidationError):
        parse_setting_value(key, raw)


def test_describe_setting():
    assert describe_setting("confirm_two_step", True) == "да"
    assert describe_setting("max_donation", Decimal("5.00")) == "5"
    assert describe_setting("support_contact", None) == "—"


@pytest.mark.integration
async def test_database_timezone_override_is_loaded(session_factory, env_settings):
    async with session_scope(session_factory) as session:
        await settings_repo.set_value(session, "timezone", "Europe/Moscow", updated_by=100)
        await settings_repo.set_value(session, "max_donation", "123.45", updated_by=100)
    async with session_factory() as session:
        runtime = await load_runtime_settings(session, env_settings)
        assert runtime.timezone_name == "Europe/Moscow"
        assert runtime.max_donation == Decimal("123.45")
