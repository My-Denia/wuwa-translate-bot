# Deployment

## VPS Docker Compose

The VPS target uses Docker Compose because the current system Python there is
older than the project target. Copy the repo to `/opt/wuwaterm/current`, create
`/opt/wuwaterm/current/.env` from `deploy/env.example`, and set it to mode
`600`. Secrets are injected only through Compose `env_file`; `.env` is ignored
and excluded from the image build context.

Prepare or refresh data without starting the service:

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml run --rm wuwaterm refresh-data
docker compose -f deploy/docker-compose.yml run --rm wuwaterm build-db --atomic
docker compose -f deploy/docker-compose.yml run --rm wuwaterm verify-db
```

For each real game-version refresh, pick at least one term that exists only in
the new game data and run a live `/tr <term>` check in Telegram after the DB
build. Counts and hashes prove rebuild mechanics; a new-term live check proves
the running bot is serving the refreshed content.

The compose service uses long polling (`wuwaterm bot`) and
`restart: unless-stopped`. It does not configure webhook delivery, inline query
handling, or any extra command-routing layer. Starting the service is
owner-gated:

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml up -d
```

## Deployment Smoke

After the service starts, use `scripts/deploy_smoke.py` as a deployment
reachability check. It verifies `getMe`, and when `TELEGRAM_TEST_CHAT_ID` is set
it sends one diagnostic message without printing the token or chat id. See
[Validation](validation.md) for live smoke caveats.
