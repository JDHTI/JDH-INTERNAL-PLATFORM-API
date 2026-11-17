#!/usr/bin/env bash
# Builds and starts the API on this server, runs the migrations, then checks it
# through Caddy.
#
#   deploy/deploy.sh
#
# Runs from any checkout of the repo (the runner's workspace or a manual copy):
# the compose project name is fixed, so the containers and volumes are the same.
# Secrets and per-server values live in ~/apps/platform/api.env (template:
# deploy/api.env.example). See docs/deploy.md.
set -euo pipefail

cd "$(dirname "$0")/.."

export PLATFORM_API_ENV=${PLATFORM_API_ENV:-$HOME/apps/platform/api.env}
[[ -f $PLATFORM_API_ENV ]] || {
  echo "Missing $PLATFORM_API_ENV (copy deploy/api.env.example and fill it in)" >&2
  exit 1
}
# Compose interpolates the passwords of the dependencies from the shell.
set -a; . "$PLATFORM_API_ENV"; set +a
export PUBLIC_HOST=${PUBLIC_HOST:-intranet.jdh.com.co}
export EDGE_NETWORK=${EDGE_NETWORK:-intranet-jdh_default}
# TLS for *.jdh.com.co ends at the infrastructure proxy, which forwards plain HTTP to this
# server's Caddy: the check takes that same hop.
EDGE_HTTP=${EDGE_HTTP:-http://127.0.0.1}
export GIT_SHA=${GIT_SHA:-$(git rev-parse --short HEAD 2>/dev/null || echo unknown)}

COMPOSE=(docker compose -f deploy/compose.prod.yaml)

# Shared with the UI and edge stack deploys: two builds on one host at once
# starve each other, and a backup must not run mid-restart.
exec 9>"${DEPLOY_LOCK:-$HOME/apps/.deploy.lock}"
flock 9

docker network inspect "$EDGE_NETWORK" >/dev/null 2>&1 \
  || { echo "Missing network $EDGE_NETWORK: start the edge stack first" >&2; exit 1; }

# Every deploy may migrate the database: back it up first (not on the very first deploy).
if [[ -n $(docker ps -q --filter label=com.docker.compose.project=internal-platform-api \
  --filter label=com.docker.compose.service=postgres) ]]; then
  echo "> Backup"
  PLATFORM_DEPLOY_LOCKED=1 bash deploy/backup.sh pre-deploy
fi

# The rate limiter trusts X-Forwarded-For only from the edge network (Caddy, and the UI
# when it renders pages on the server): without it every person would share one bucket.
if [[ -z ${RATE_LIMIT_TRUSTED_PROXY_IPS:-} ]]; then
  RATE_LIMIT_TRUSTED_PROXY_IPS=$(docker network inspect "$EDGE_NETWORK" \
    --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' | xargs | tr ' ' ',')
  export RATE_LIMIT_TRUSTED_PROXY_IPS
fi

echo "> Build $GIT_SHA"
"${COMPOSE[@]}" build api

echo "> Dependencies"
"${COMPOSE[@]}" up -d --wait postgres redis minio clamav
"${COMPOSE[@]}" up minio-init

echo "> Migrations"
"${COMPOSE[@]}" run --rm migrate

echo "> Up"
"${COMPOSE[@]}" up -d --wait api

echo "> Check through Caddy"
# /api/v1/* is the only API path Caddy routes; without a session /users/me answers 401,
# which proves Caddy reaches the API (a missing API answers 502).
for _ in $(seq 1 40); do
  status=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $PUBLIC_HOST" \
    "$EDGE_HTTP/api/v1/users/me" || true)
  if [[ $status == 401 ]]; then
    docker image prune -f >/dev/null
    echo "> Deployed $GIT_SHA"
    exit 0
  fi
  sleep 3
done

echo "The API did not answer through Caddy at $EDGE_HTTP/api/v1/users/me (Host: $PUBLIC_HOST, last: $status)" >&2
"${COMPOSE[@]}" ps
"${COMPOSE[@]}" logs --tail 80 api
exit 1
