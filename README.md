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
- `RULES_URL`, `PRIVACY_URL` — required consent links. The privacy policy itself is shown in the bot (`/privacy`) before consent; keep the public page identical to that text and raise `PRIVACY_VERSION` when it changes.
- `OPERATOR_NAME`, `OPERATOR_ADDRESS` — who processes personal data. Participation stays closed until both are set. `STORAGE_COUNTRY` is where the database and receipts are stored (`Беларусь` if they stay in Belarus).

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

## Manual smoke test (Telegram test bot)

1. Create a test bot with BotFather and put its token in `BOT_TOKEN`; put your Telegram ID in
   `ADMIN_IDS`.
2. Create a private test group for organization review, add the bot, and copy its chat ID with
   `/chatid` from the group (or from the group ID shown by Telegram).
3. Start the bot, run `/admin`, create an organization with an active payment method and a
   review chat, then create and activate a raffle.
4. Open the bot from a second account, accept consent, choose the organization and method, enter
   an amount, and upload a PDF/photo receipt.
5. Confirm the receipt from the review chat (or from `/admin` → Applications in DM). Verify that
   the user receives ticket numbers, `/my` shows ranges, and the card is edited.
6. Finish the raffle, export CSV/XLSX/PDF, and record a winner.

Never run a real production raffle against the test bot.

## VS Code

The repository includes `.vscode/launch.json`, `.vscode/tasks.json`, and recommended settings.

1. Open the repository folder in VS Code.
2. Install the recommended extensions (Python, Python Debugger, Ruff).
3. Select `.venv/bin/python` as the interpreter if VS Code has not done so automatically.
4. Press **F5** and choose **Bot: run**.

The launch configuration loads `.env`, runs `uv sync`, starts PostgreSQL and RustFS with
`docker compose up -d --wait`, applies Alembic migrations, and then starts `bot.main`.

Other configurations:

- **Bot: run (no debug)** — start without breakpoints.
- **Tests: pytest** — run the full suite under the debugger.
- **Tests: current file** — run the file open in the editor.
- **Alembic: upgrade head** — apply migrations.

The `quality: ruff + tests` task runs all quality gates.

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
The bot container waits for both and then applies migrations. The bot exposes a readiness
endpoint on `HEALTH_PORT` (default `8081`); it returns `200` when PostgreSQL and RustFS are
reachable and `503` otherwise.

## Production deployment

Production runs on a shared VPS from `/opt/raffle-tg-bot` with
`compose.prod.yaml`, which publishes no database or RustFS ports. Every push to
`main` triggers the GitHub Actions workflow: quality gates, then an SSH deploy
that fast-forwards the checkout, rebuilds the image, applies Alembic migrations
and restarts the bot with `scripts/deploy.sh`.

See [deploy/PRODUCTION.md](deploy/PRODUCTION.md) for the server layout, secrets
and the one-time bootstrap.

## Backups

`scripts/backup.sh` creates a compressed PostgreSQL dump and a RustFS data archive in
`BACKUP_DIR` (default `./backups`), keeping 14 days by default. `scripts/restore.sh` is
destructive and requires `CONFIRM=yes`. Schedule backups with cron or a systemd timer, copy them
off-host, encrypt them (they contain personal data) and test restores regularly.

```bash
BACKUP_DIR=/srv/backups ./scripts/backup.sh
CONFIRM=yes ./scripts/restore.sh backups/postgres_*.dump.gz backups/rustfs_*.tar.gz
```

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
