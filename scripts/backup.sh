#!/usr/bin/env bash
# Consistent backup of everything the factory knows, taken while it runs:
#   factory.db (orders, runs, events, workflows, skills) via SQLite's online backup,
#   auth.db (users, token hashes, audit) the same way, and the rest of .factory-data
#   (product repos, holdout scenarios, run worktrees, artifacts).
# Usage: make backup   → backups/agent-factory-<timestamp>/
set -euo pipefail
cd "$(dirname "$0")/.."
ts=$(date +%Y%m%d-%H%M%S)
out="backups/agent-factory-$ts"
mkdir -p "$out"

snapshot='import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); print("ok", sys.argv[1])'
docker compose exec -T -u factory factory python3 -c "$snapshot" /data/factory.db /data/.backup-factory.db
docker compose exec -T auth python3 -c "$snapshot" /data/auth.db /data/.backup-auth.db
docker compose cp auth:/data/.backup-auth.db "$out/auth.db"
docker compose exec -T auth rm -f /data/.backup-auth.db

# everything else in .factory-data, with the consistent DB copy instead of the live files
tar -czf "$out/factory-data.tgz" -C .factory-data \
  --exclude=./factory.db --exclude=./factory.db-wal --exclude=./factory.db-shm \
  --exclude=./.backup-factory.db --exclude=./sandbox --exclude=./kube .
mv .factory-data/.backup-factory.db "$out/factory.db"
git rev-parse --short HEAD > "$out/factory-version.txt" 2>/dev/null || true
echo "backup written to $out ($(du -sh "$out" | cut -f1))"
