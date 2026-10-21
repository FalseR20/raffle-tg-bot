#!/usr/bin/env bash
# Daily backup example for PostgreSQL and RustFS.
#
# Usage:
#   BACKUP_DIR=/srv/backups ./scripts/backup.sh
#
# Schedule it with cron or a systemd timer. Test restore regularly with
# scripts/restore.sh (it requires an explicit confirmation).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
POSTGRES_USER="${POSTGRES_USER:-raffle}"
POSTGRES_DB="${POSTGRES_DB:-raffle}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"

echo "Backing up PostgreSQL to $BACKUP_DIR/postgres_$STAMP.dump.gz"
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --format=custom --compress=9 > "$BACKUP_DIR/postgres_$STAMP.dump.gz"

echo "Backing up RustFS volume to $BACKUP_DIR/rustfs_$STAMP.tar.gz"
docker compose exec -T rustfs tar -czf - /data > "$BACKUP_DIR/rustfs_$STAMP.tar.gz"

find "$BACKUP_DIR" -type f -name 'postgres_*.dump.gz' -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -type f -name 'rustfs_*.tar.gz' -mtime +"$RETENTION_DAYS" -delete

echo "Backup complete. Keep copies off-host; both archives contain personal data."
