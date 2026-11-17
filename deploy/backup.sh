#!/usr/bin/env bash
# Backup of the platform's data in ~/backups/platform/<date>-<label>/:
#   internal_platform.dump  (the platform database, pg_dump -Fc)
#   storage.tar.gz          (RustFS bucket: PQRSF attachments, documents, photos)
#
#   deploy/backup.sh [label]      # label: daily (default), pre-deploy, manual…
#
# Its own folder, apart from the intranet's backups: the edge stack's backup.sh prunes every
# ~/backups/*-daily folder and would count these as its own.
#
# Keeps 14 daily and 10 pre-deploy backups. Daily cron (crontab -e of the server user):
#   30 2 * * * ~/apps/JDH-INTERNAL-PLATFORM-API/deploy/backup.sh daily >> ~/backups/platform/backup.log 2>&1
#
# This covers deletions and mistakes, not the loss of the server: ~/backups must also be
# copied off this VM. Restore steps: docs/deploy.md.
set -euo pipefail

cd "$(dirname "$0")/.."

PLATFORM_API_ENV=${PLATFORM_API_ENV:-$HOME/apps/platform/api.env}
[[ -f $PLATFORM_API_ENV ]] || { echo "Missing $PLATFORM_API_ENV" >&2; exit 1; }
set -a; . "$PLATFORM_API_ENV"; set +a
db_user=${POSTGRES_USER:-platform}
db_name=${POSTGRES_DB:-internal_platform}
project=internal-platform-api

# A deploy restarting the database must not run at the same time; deploy.sh already holds
# the lock when it calls this script.
if [[ -z ${PLATFORM_DEPLOY_LOCKED:-} ]]; then
  exec 9>"${DEPLOY_LOCK:-$HOME/apps/.deploy.lock}"
  flock -w 1800 9 || { echo "Could not take the deploy lock" >&2; exit 1; }
fi

postgres=$(docker ps -q \
  --filter "label=com.docker.compose.project=$project" \
  --filter "label=com.docker.compose.service=postgres")
[[ -n $postgres ]] || { echo "The $project postgres container is not running" >&2; exit 1; }

label=${1:-daily}
root="$HOME/backups/platform"
dest="$root/$(date +%Y%m%d-%H%M%S)-$label"
mkdir -p "$dest"
chmod 700 "$HOME/backups" "$root" "$dest" 2>/dev/null || true

docker exec "$postgres" pg_dump -U "$db_user" -Fc "$db_name" > "$dest/$db_name.dump.partial"
mv "$dest/$db_name.dump.partial" "$dest/$db_name.dump"

# The object storage volume, read live by a throwaway container.
volume="${project}_minio_data"
if docker volume inspect "$volume" >/dev/null 2>&1; then
  docker run --rm -v "$volume:/src:ro" -v "$dest:/out" alpine:3 \
    tar -czf /out/storage.tar.gz -C /src .
fi

keep() {
  local pattern=$1 count=$2
  # Dated names sort in time order: reversed, the newest come first.
  find "$root" -mindepth 1 -maxdepth 1 -type d -name "*-$pattern" -printf '%f\n' \
    | sort -r | tail -n +$((count + 1)) | sed "s|^|$root/|" | xargs -r rm -rf
}
keep daily 14
keep pre-deploy 10

du -sh "$dest" | awk '{print "Backup: " $2 " (" $1 ")"}'
