"""Shared pytest fixtures. Integration tests use real PostgreSQL and RustFS."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.db.session import create_engine, create_session_factory

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_URL = "postgresql+asyncpg://raffle:raffle@localhost:5433/raffle_test"
TEST_URL = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL)

TRUNCATE_SQL = """
TRUNCATE TABLE winners, audit_log, applications, participant_totals,
    raffle_organizations, raffles, payment_methods, roles, invites,
    organizations, users, settings
RESTART IDENTITY CASCADE
"""


def _parts():
    url = make_url(TEST_URL)
    return {
        "host": url.host or "localhost",
        "port": url.port or 5432,
        "user": url.username or "raffle",
        "password": url.password or "raffle",
        "database": url.database or "raffle_test",
    }


async def _create_database_if_missing() -> None:
    parts = _parts()
    connection = await asyncpg.connect(
        host=parts["host"],
        port=parts["port"],
        user=parts["user"],
        password=parts["password"],
        database="postgres",
    )
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", parts["database"]
        )
        if not exists:
            await connection.execute(f'CREATE DATABASE "{parts["database"]}"')
    finally:
        await connection.close()


async def _reset_schema() -> None:
    parts = _parts()
    connection = await asyncpg.connect(**parts)
    try:
        await connection.execute("DROP SCHEMA public CASCADE")
        await connection.execute("CREATE SCHEMA public")
    finally:
        await connection.close()


async def _prepare_database() -> None:
    await _create_database_if_missing()
    await _reset_schema()


def pytest_sessionstart(session) -> None:
    try:
        asyncio.run(_prepare_database())
    except Exception as exc:  # pragma: no cover - environment failure
        pytest.exit(
            f"Cannot prepare PostgreSQL test database at {TEST_URL}: {exc}. "
            "Start it with `docker compose up -d postgres`.",
            returncode=1,
        )
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_URL
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:  # pragma: no cover - environment failure
        pytest.exit(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}", returncode=1)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    db_engine = create_engine(TEST_URL)
    try:
        yield db_engine
    finally:
        await db_engine.dispose()


@pytest_asyncio.fixture
async def session_factory(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as connection:
        await connection.execute(text(TRUNCATE_SQL))
        await connection.execute(text("ALTER SEQUENCE application_public_id_seq RESTART WITH 1"))
    yield create_session_factory(engine)


@pytest_asyncio.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as db_session:
        yield db_session
        await db_session.rollback()


@pytest.fixture
def env_settings() -> Settings:
    return Settings(
        _env_file=None,
        bot_token="123456:TEST",
        admin_ids=(100, 101),
        database_url=TEST_URL,
        rustfs_endpoint=os.environ.get("TEST_RUSTFS_ENDPOINT", "http://localhost:9010"),
        rustfs_access_key=os.environ.get("TEST_RUSTFS_ACCESS_KEY", "raffle-minio"),
        rustfs_secret_key=os.environ.get("TEST_RUSTFS_SECRET_KEY", "raffle-minio-secret"),
        rustfs_bucket=os.environ.get("TEST_RUSTFS_BUCKET", "raffle-test-receipts"),
        rules_url="https://example.org/rules",
        privacy_url="https://example.org/privacy",
        operator_name="Тестовый организатор",
        operator_address="г. Минск, ул. Примерная, 1",
        storage_country="Беларусь",
        channel_url="https://t.me/test_channel",
        channel_title="Тестовый канал",
        support_contact="@support",
        management_log_chat_id=-100999,
        log_level="WARNING",
    )
