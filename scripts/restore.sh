#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh. Stops the factory first; your current
# .factory-data is kept as .factory-data.before-restore-<timestamp> (nothing deleted).
# An encrypted backup (.tar.age) needs the age key: BACKUP_AGE_IDENTITY=~/.agent-factory-backup.key
# Usage: make restore BACKUP=backups/agent-factory-<timestamp>.tar.age   (or a plaintext folder)
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
src="${1:?usage: scripts/restore.sh backups/agent-factory-<timestamp>.tar.age}"
from_env() {  # a setting from the environment, else from .env (the compose file's settings)
  local v="${!1:-}"
  if [ -z "$v" ] && [ -f .env ]; then v=$(sed -n "s/^$1=//p" .env | tail -1); fi
  printf '%s' "$v"
}
if [ -f "$src" ] && [ "${src%.age}" != "$src" ]; then
  key=$(from_env BACKUP_AGE_IDENTITY)
  key="${key/#\~/$HOME}"
  [ -n "$key" ] || { echo "set BACKUP_AGE_IDENTITY to the age key file the backup was encrypted for"; exit 2; }
  command -v age >/dev/null || { echo "'age' is not installed (macOS: brew install age)"; exit 2; }
  plain=$(mktemp -d backups/.restore-XXXXXX)
  trap 'rm -rf "$plain"' EXIT
  age -d -i "$key" "$src" | tar -xf - -C "$plain"
  src="$plain"
fi
for f in factory.db auth.db factory-data.tgz; do test -f "$src/$f" || { echo "missing $src/$f"; exit 1; }; done
echo "restoring $src (factory version $(cat "$src/factory-version.txt" 2>/dev/null || echo unknown))"
docker compose stop factory auth web
ts=$(date +%Y%m%d-%H%M%S)
if [ -d .factory-data ]; then mv .factory-data ".factory-data.before-restore-$ts"; fi
mkdir -p .factory-data
tar -xzf "$src/factory-data.tgz" -C .factory-data
cp "$src/factory.db" .factory-data/factory.db
# auth.db goes in on stdin: no bind mount, so the folder can stay readable by its owner only
docker compose run --rm --no-deps -T --entrypoint sh auth \
  -c 'cat > /data/auth.db && rm -f /data/auth.db-wal /data/auth.db-shm' < "$src/auth.db"
docker compose up -d
echo "restored. Previous data kept in .factory-data.before-restore-$ts"
