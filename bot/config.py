"""Application configuration loaded from the environment.

The environment is the source of truth for secrets and bootstrap values.
Runtime-tunable product settings live in the ``settings`` table and are
merged on top of these defaults by :mod:`bot.services.settings`.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from bot.utils.url import is_valid_http_url


class ConfigurationError(RuntimeError):
    """Raised when the environment configuration is invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str
    admin_ids: Annotated[tuple[int, ...], NoDecode]
    database_url: str

    rustfs_endpoint: str
    rustfs_access_key: str
    rustfs_secret_key: str
    rustfs_bucket: str = "raffle-receipts"
    rustfs_region: str = "us-east-1"
    rustfs_use_ssl: bool = True
    rustfs_force_path_style: bool = True

    tz: str = "Europe/Minsk"
    default_ticket_price: Decimal = Decimal("5.00")
    default_min_amount: Decimal = Decimal("5.00")
    max_donation: Decimal = Decimal("10000.00")
    receipt_max_mb: int = 20
    receipt_retention_days: int = 90

    channel_url: str | None = None
    channel_title: str = "Тетрадь с полями🪶"
    rules_url: str
    privacy_url: str
    rules_version: str = "1"
    privacy_version: str = "2"
    operator_name: str | None = None
    operator_address: str | None = None
    storage_country: str | None = None
    support_contact: str | None = None
    management_log_chat_id: int | None = None

    enable_random_draw: bool = False
    confirm_two_step: bool = True
    require_age_confirmation: bool = False

    webhook_url: str | None = None
    webhook_secret: str | None = None
    webhook_path: str = "/webhook"
    webhook_port: int = 8443
    health_port: int = 8081
    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            ids = [part.strip() for part in value.split(",") if part.strip()]
            return tuple(int(part) for part in ids)
        return value

    @field_validator("admin_ids")
    @classmethod
    def _require_admins(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("ADMIN_IDS must contain at least one Telegram ID")
        return value

    @field_validator("database_url")
    @classmethod
    def _require_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "DATABASE_URL must start with postgresql+asyncpg://; "
                "SQLite and other drivers are not supported"
            )
        return value

    @field_validator("management_log_chat_id", mode="before")
    @classmethod
    def _empty_int_to_none(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, str):
            return int(value)
        return value

    @field_validator(
        "channel_url",
        "support_contact",
        "operator_name",
        "operator_address",
        "storage_country",
        "webhook_url",
        "webhook_secret",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("channel_url", "rules_url", "privacy_url")
    @classmethod
    def _validate_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not is_valid_http_url(value):
            raise ValueError("URL must start with http:// or https:// and contain a host")
        return value

    @field_validator("tz")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value

    @field_validator("health_port", "webhook_port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("Ports must be between 1 and 65535")
        return value

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _validate_financial_defaults(self) -> Settings:
        if self.default_ticket_price <= 0:
            raise ValueError("DEFAULT_TICKET_PRICE must be positive")
        if self.default_min_amount < self.default_ticket_price:
            raise ValueError("DEFAULT_MIN_AMOUNT must be >= DEFAULT_TICKET_PRICE")
        if self.max_donation <= 0:
            raise ValueError("MAX_DONATION must be positive")
        if not 1 <= self.receipt_max_mb <= 20:
            raise ValueError("RECEIPT_MAX_MB must be between 1 and 20")
        if self.receipt_retention_days < 1:
            raise ValueError("RECEIPT_RETENTION_DAYS must be positive")
        return self

    @property
    def admin_id_set(self) -> frozenset[int]:
        return frozenset(self.admin_ids)

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def receipt_max_bytes(self) -> int:
        return self.receipt_max_mb * 1024 * 1024

    @property
    def sync_database_url(self) -> str:
        """URL for tools (Alembic) that use a synchronous-style config string."""
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings, converting validation errors into a clear error."""
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:  # pydantic ValidationError and friends
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc


def reset_settings_cache() -> None:
    get_settings.cache_clear()
