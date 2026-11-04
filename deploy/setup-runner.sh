#!/usr/bin/env bash
# Registers this server as the self-hosted runner of JDH-INTERNAL-PLATFORM-API and
# installs it as a service (asks for sudo). Idempotent.
#
#   deploy/setup-runner.sh <registration-token>
#
# The token comes from GitHub: repo, Settings, Actions, Runners, New
# self-hosted runner (it expires in one hour and needs repo admin).
set -euo pipefail

TOKEN=${1:?Missing the registration token}
URL=https://github.com/JDHTI/JDH-INTERNAL-PLATFORM-API
LABEL=internal-platform-api
DIR="$HOME/actions-runner/JDH-INTERNAL-PLATFORM-API"

id -nG | grep -qw docker || { echo "$USER is not in the docker group" >&2; exit 1; }
command -v flock >/dev/null || { echo "Missing flock (util-linux)" >&2; exit 1; }

if [[ ! -x $DIR/config.sh ]]; then
  version=$(curl -fsS https://api.github.com/repos/actions/runner/releases/latest \
    | grep -oE '"tag_name": *"v[0-9.]+"' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
  mkdir -p "$DIR"
  curl -fsSL "https://github.com/actions/runner/releases/download/v$version/actions-runner-linux-x64-$version.tar.gz" \
    | tar -xz -C "$DIR"
fi

cd "$DIR"
./config.sh --unattended --replace --url "$URL" --token "$TOKEN" \
  --name "$(hostname)-platform-api" --labels "$LABEL" --work _work
# Each runner is its own service: the UI's runner keeps running.
sudo ./svc.sh install "$USER"
sudo ./svc.sh start
sudo ./svc.sh status | head -n 5
