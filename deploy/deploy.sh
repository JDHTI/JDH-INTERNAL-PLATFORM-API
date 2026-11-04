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
export PUBLIC_HOST=${PUBLIC_HOST:-intranet.jdh.local}
export EDGE_NETWORK=${EDGE_NETWORK:-intranet-jdh_default}
EDGE_CA=${EDGE_CA:-$HOME/apps/INTRANET-JDH-API/deploy/caddy/ca/root.crt}
export GIT_SHA=${GIT_SHA:-$(git rev-parse --short HEAD 2>/dev/null || echo unknown)}

COMPOSE=(docker compose -f deploy/compose.prod.yaml)

# Shared with the UI and edge stack deploys: two builds on one host at once
# starve each other, and a backup must not run mid-restart.
exec 9>"${DEPLOY_LOCK:-$HOME/apps/.deploy.lock}"
flock 9

docker network inspect "$EDGE_NETWORK" >/dev/null 2>&1 \
  || { echo "Missing network $EDGE_NETWORK: start the edge stack first" >&2; exit 1; }
[[ -f $EDGE_CA ]] || { echo "Missing the Caddy root CA at $EDGE_CA" >&2; exit 1; }

# The rate limiter trusts X-Forwarded-For only from Caddy: without it every person
# would share Caddy's address and one bucket.
if [[ -z ${RATE_LIMIT_TRUSTED_PROXY_IPS:-} ]]; then
  RATE_LIMIT_TRUSTED_PROXY_IPS=$(docker network inspect "$EDGE_NETWORK" \
    --format '{{range .Containers}}{{if eq .Name "intranet-caddy"}}{{.IPv4Address}}{{end}}{{end}}' \
    | cut -d/ -f1)
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
  status=$(curl -s -o /dev/null -w '%{http_code}' --cacert "$EDGE_CA" \
    --resolve "$PUBLIC_HOST:443:127.0.0.1" "https://$PUBLIC_HOST/api/v1/users/me" || true)
  if [[ $status == 401 ]]; then
    docker image prune -f >/dev/null
    echo "> Deployed $GIT_SHA"
    exit 0
  fi
  sleep 3
done

echo "The API did not answer through Caddy at https://$PUBLIC_HOST/api/v1/users/me (last: $status)" >&2
"${COMPOSE[@]}" ps
"${COMPOSE[@]}" logs --tail 80 api
exit 1
