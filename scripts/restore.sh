#!/usr/bin/env bash
# Restore PostgreSQL and RustFS from backups created by scripts/backup.sh.
#
# DESTRUCTIVE. Stop the bot first and set CONFIRM=yes:
#
#   docker compose stop bot
#   CONFIRM=yes ./scripts/restore.sh backups/postgres_*.dump.gz backups/rustfs_*.tar.gz
#
set -euo pipefail

if [[ "${CONFIRM:-}" != "yes" ]]; then
  echo "Refusing to restore without CONFIRM=yes" >&2
  exit 1
fi

PG_BACKUP="${1:?usage: restore.sh <postgres.dump.gz> <rustfs.tar.gz>}"
RUSTFS_BACKUP="${2:?usage: restore.sh <postgres.dump.gz> <rustfs.tar.gz>}"
POSTGRES_USER="${POSTGRES_USER:-raffle}"
POSTGRES_DB="${POSTGRES_DB:-raffle}"

echo "Restoring PostgreSQL"
gunzip -c "$PG_BACKUP" | docker compose exec -T postgres \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner

echo "Restoring RustFS"
docker compose exec -T rustfs sh -c 'rm -rf /data/*'
docker compose exec -T rustfs tar -xzf - -C /

echo "Restore complete. Start the bot and run the integrity check."
