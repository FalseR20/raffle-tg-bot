# Production deploy

The bot runs on a shared VPS (`93.115.19.73`) next to another Compose project.
Every push to `main` triggers GitHub Actions: quality gates run, then the
`deploy` job connects to the VPS as `deploy`, fast-forwards
`/opt/raffle-tg-bot`, rebuilds the image, applies Alembic migrations and
restarts the bot.

The previous container keeps running until the new image is built and
migrations succeed, so a failed deploy does not take the bot down.

## Layout on the server

| Path | Purpose |
| --- | --- |
| `/opt/raffle-tg-bot` | git checkout owned by `deploy` |
| `/opt/raffle-tg-bot/.env` | production secrets, mode `600`, untracked |
| `compose.prod.yaml` | production stack (no published DB/S3 ports) |
| `scripts/deploy.sh` | build → migrate → restart |
| `/opt/my-beer-list` | unrelated project; do not touch |

Only the bot health endpoint is published (`127.0.0.1:8081`). PostgreSQL and
RustFS are reachable only on the Compose network.

## One-time bootstrap

1. Clone the repository as `deploy`:

   ```bash
   install -d -o deploy -g deploy /opt/raffle-tg-bot
   sudo -u deploy -H git clone https://github.com/FalseR20/raffle-tg-bot.git /opt/raffle-tg-bot
   ```

2. Create `/opt/raffle-tg-bot/.env` from `.env.example`, owned by `deploy` with
   mode `600`. Generate unique `POSTGRES_PASSWORD`, `RUSTFS_ACCESS_KEY` and
   `RUSTFS_SECRET_KEY` values (`openssl rand -hex 24`); set
   `RUSTFS_ENDPOINT=http://rustfs:9000` and point `DATABASE_URL` at the
   `postgres` service. Compose overrides both at runtime, but keeping them
   consistent avoids surprises when running commands by hand.

3. Create an SSH key for GitHub Actions and authorize it:

   ```bash
   ssh-keygen -t ed25519 -f /home/deploy/.ssh/github_actions -N "" -C github-actions-raffle
   cat /home/deploy/.ssh/github_actions.pub >> /home/deploy/.ssh/authorized_keys
   chmod 600 /home/deploy/.ssh/authorized_keys
   ```

4. Store the private key and connection details as secrets in the `production`
   GitHub environment (not repository secrets, so pull requests cannot read
   them):

   ```bash
   gh api --method PUT repos/FalseR20/raffle-tg-bot/environments/production
   gh secret set PROD_SSH_HOST --env production --body 93.115.19.73
   gh secret set PROD_SSH_USER --env production --body deploy
   gh secret set PROD_SSH_PORT --env production --body 22
   gh secret set PROD_SSH_KEY --env production < /home/deploy/.ssh/github_actions
   ```

5. First deploy: push to `main` or run the workflow manually with
   `gh workflow run CI`.

## Day-to-day

```bash
git push origin main
```

`gh run watch` follows the run. On the server:

```bash
cd /opt/raffle-tg-bot
docker compose -f compose.prod.yaml --env-file .env ps
docker compose -f compose.prod.yaml --env-file .env logs -f bot
curl -fsS http://127.0.0.1:8081/
```

Do not run `git clean -fd` in `/opt/raffle-tg-bot`: it deletes `.env` and the
named volumes are not affected, but the checkout would lose its secrets file.

## Backups

`scripts/backup.sh` uses Compose defaults from `compose.yaml`, which does not
match the production stack. For production run:

```bash
docker compose -f compose.prod.yaml --env-file .env exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > raffle-$(date +%F).sql.gz
```

Copy the dump and the `rustfs-data` volume off the host and test restores.
