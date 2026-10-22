#!/usr/bin/env bash
# Build and restart the production stack in the current checkout.
#
# The caller is expected to have updated the working tree already (the GitHub
# Actions workflow does `git fetch` + `git reset --hard origin/main`). This
# script only rebuilds images, applies migrations and restarts the bot, so the
# previous version keeps running if a build or migration fails.
#
# Usage: bash scripts/deploy.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
  echo "error: $ROOT/.env is missing; create it from .env.example" >&2
  exit 1
fi

COMPOSE=(docker compose -f compose.prod.yaml --env-file .env)

echo "==> starting dependencies"
"${COMPOSE[@]}" up -d --wait --wait-timeout 180 postgres rustfs

echo "==> building bot image"
"${COMPOSE[@]}" build bot

echo "==> applying migrations"
"${COMPOSE[@]}" run --rm --no-deps bot alembic upgrade head

echo "==> restarting bot"
"${COMPOSE[@]}" up -d --no-build bot

echo "==> status"
"${COMPOSE[@]}" ps

docker image prune -f >/dev/null
