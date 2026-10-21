# Telegram Charity Raffle Bot

Telegram bot for charity raffles in Belarusian rubles (BYN). Participants choose an
organization, pay the organization directly, upload a receipt, and receive ticket numbers
only after an organization representative confirms the payment. The bot never accepts or
holds money.

The product UI is in Russian. All code, comments and technical documentation are in English.

## Stack

- Python 3.11+ (pinned to 3.12), [uv](https://docs.astral.sh/uv/)
- aiogram 3.x, SQLAlchemy 2.x async, asyncpg
- PostgreSQL 15+ (required everywhere, no SQLite fallback)
- RustFS (S3-compatible) for receipt files, aioboto3
- Alembic, APScheduler, openpyxl, reportlab, pypdf
- Docker Compose

## Documentation

- [AI_AGENT_SPEC.md](AI_AGENT_SPEC.md) — implementation contract
- [ASSUMPTIONS.md](ASSUMPTIONS.md) — assumptions and open questions
- [AGENTS.md](AGENTS.md) — guide for coding agents

## Quick start (local development)

1. Install prerequisites: Python 3.12, `uv`, Docker.
2. Copy the environment file and fill in the required values:

   ```bash
   cp .env.example .env
   ```

3. Start PostgreSQL and RustFS:

   ```bash
   docker compose up -d postgres rustfs
   ```

4. Install dependencies and apply migrations:

   ```bash
   uv sync --all-groups
   uv run alembic upgrade head
   ```

5. Run the bot:

   ```bash
   uv run python -m bot.main
   ```

The default compose mapping exposes PostgreSQL on `localhost:5433` and RustFS on
`localhost:9010` (console `9011`) to avoid clashing with other local services. Override with
`POSTGRES_PORT`, `RUSTFS_PORT` and `RUSTFS_CONSOLE_PORT`.

## Environment

`.env.example` documents every variable. Notable requirements:

- `BOT_TOKEN` — BotFather token.
- `ADMIN_IDS` — comma-separated Telegram IDs; the environment list is the source of truth.
- `DATABASE_URL` — must start with `postgresql+asyncpg://`.
- `RUSTFS_*` — private bucket credentials and endpoint. Production must use TLS and unique
  credentials.
- `RULES_URL`, `PRIVACY_URL` — required consent links.

The bot fails fast with a clear message if required settings are missing or invalid.

## Database migrations

```bash
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe change"
```

Migrations run against PostgreSQL only. Docker Compose runs `alembic upgrade head` before
starting the bot container.

## Tests

Tests require a real PostgreSQL instance and use a separate database
(`raffle_test` by default). RustFS tests run when the test endpoint is reachable.

```bash
docker compose up -d postgres rustfs
uv run pytest
```

Relevant environment overrides:

- `TEST_DATABASE_URL` (default `postgresql+asyncpg://raffle:raffle@localhost:5433/raffle_test`)
- `TEST_RUSTFS_ENDPOINT` (default `http://localhost:9010`)
- `TEST_RUSTFS_ACCESS_KEY`, `TEST_RUSTFS_SECRET_KEY`
- `TEST_RUSTFS_BUCKET` (default `raffle-test-receipts`)

Unit tests never touch the network. Integration and concurrency tests use real PostgreSQL.

## Quality gates

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

A pre-commit hook is provided in `.githooks/`:

```bash
git config core.hooksPath .githooks
```

## Docker

```bash
docker compose up --build
```

Compose starts PostgreSQL 15+, RustFS and the bot. PostgreSQL and RustFS data live in named
volumes. There is no permanent local receipt directory: receipts are uploaded to the private
RustFS bucket, and PostgreSQL stores metadata plus `receipt_object_key` only.

Health endpoints: `docker compose ps` shows container healthchecks for PostgreSQL and RustFS.
The bot container waits for both and then applies migrations.

## Deployment notes

- One bot instance and one scheduler instance (distributed lock required if scaled).
- Use long polling by default; set `WEBHOOK_URL` and `WEBHOOK_SECRET` to switch to webhooks.
- Terminate TLS in front of RustFS, never expose its console publicly, rotate credentials.
- Schedule daily PostgreSQL and RustFS bucket backups and test restores.
- Receipt objects are deleted after `RECEIPT_RETENTION_DAYS` past raffle finish via the
  scheduler; metadata and audit records remain.
- Legal review of raffle rules, charity/tax requirements and personal data processing is
  required before launch.

## License

MIT, see [LICENSE](LICENSE).
