import pytest
from pydantic import ValidationError

from bot.config import Settings


def build_settings(**overrides):
    values = {
        "bot_token": "123:TEST",
        "admin_ids": "100, 101",
        "database_url": "postgresql+asyncpg://raffle:raffle@localhost:5433/raffle",
        "rustfs_endpoint": "http://localhost:9010",
        "rustfs_access_key": "key",
        "rustfs_secret_key": "secret",
        "rules_url": "https://example.org/rules",
        "privacy_url": "https://example.org/privacy",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_admin_ids_are_parsed_and_required():
    settings = build_settings()
    assert settings.admin_id_set == frozenset({100, 101})
    with pytest.raises(ValidationError):
        build_settings(admin_ids="")


def test_database_must_be_postgres_asyncpg():
    with pytest.raises(ValidationError):
        build_settings(database_url="sqlite+aiosqlite:///test.db")


def test_financial_defaults_are_validated():
    with pytest.raises(ValidationError):
        build_settings(default_ticket_price="5", default_min_amount="4")
    with pytest.raises(ValidationError):
        build_settings(max_donation="0")
    with pytest.raises(ValidationError):
        build_settings(receipt_max_mb=21)


def test_timezone_and_ports_are_validated():
    with pytest.raises(ValidationError):
        build_settings(tz="Mars/Olympus")
    with pytest.raises(ValidationError):
        build_settings(health_port=0)


def test_empty_optional_values_become_none():
    settings = build_settings(
        management_log_chat_id="", channel_url="", support_contact="", webhook_secret=""
    )
    assert settings.management_log_chat_id is None
    assert settings.channel_url is None
    assert settings.support_contact is None
    assert settings.webhook_secret is None
    assert settings.receipt_max_bytes == 20 * 1024 * 1024
