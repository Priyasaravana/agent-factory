#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh. Stops the factory first; your current
# .factory-data is kept as .factory-data.before-restore-<timestamp> (nothing deleted).
# Usage: make restore BACKUP=backups/agent-factory-<timestamp>
set -euo pipefail
cd "$(dirname "$0")/.."
src="${1:?usage: scripts/restore.sh backups/agent-factory-<timestamp>}"
for f in factory.db auth.db factory-data.tgz; do test -f "$src/$f" || { echo "missing $src/$f"; exit 1; }; done
echo "restoring $src (factory version $(cat "$src/factory-version.txt" 2>/dev/null || echo unknown))"
docker compose stop factory auth web
ts=$(date +%Y%m%d-%H%M%S)
if [ -d .factory-data ]; then mv .factory-data ".factory-data.before-restore-$ts"; fi
mkdir -p .factory-data
tar -xzf "$src/factory-data.tgz" -C .factory-data
cp "$src/factory.db" .factory-data/factory.db
docker compose run --rm --no-deps -v "$PWD/$src/auth.db:/restore/auth.db:ro" --entrypoint sh auth \
  -c 'cp /restore/auth.db /data/auth.db && rm -f /data/auth.db-wal /data/auth.db-shm'
docker compose up -d
echo "restored. Previous data kept in .factory-data.before-restore-$ts"
