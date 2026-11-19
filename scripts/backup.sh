#!/usr/bin/env bash
# Consistent backup of everything the factory knows, taken while it runs:
#   factory.db (orders, runs, events, workflows, skills) via SQLite's online backup,
#   auth.db (users, token hashes, audit) the same way, and the rest of .factory-data
#   (product repos, holdout scenarios, run worktrees, artifacts, products' own backups).
# The backup holds secrets and product data, so it is encrypted with age (S-17): set
#   BACKUP_AGE_RECIPIENT=age1…    (the public key; make the pair once with `age-keygen -o ~/.agent-factory-backup.key`)
# and keep the key file off this machine's backups folder. A plaintext folder is written only when
# you ask for it explicitly with BACKUP_PLAINTEXT=1 (it is still readable by you alone).
# Usage: make backup   → backups/agent-factory-<timestamp>.tar.age  (or backups/agent-factory-<timestamp>/)
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077  # a backup is readable by its owner only
ts=$(date +%Y%m%d-%H%M%S)
from_env() {  # a setting from the environment, else from .env (the compose file's settings)
  local v="${!1:-}"
  if [ -z "$v" ] && [ -f .env ]; then v=$(sed -n "s/^$1=//p" .env | tail -1); fi
  printf '%s' "$v"
}
recipient=$(from_env BACKUP_AGE_RECIPIENT)
if [ -z "$recipient" ] && [ "$(from_env BACKUP_PLAINTEXT)" != "1" ]; then
  echo "refused: backups hold secrets and product data and are encrypted with age." >&2
  echo "  once:  age-keygen -o ~/.agent-factory-backup.key   (keep it safe; restore needs it)" >&2
  echo "  then:  BACKUP_AGE_RECIPIENT=<its public key, age1…> in .env or your shell" >&2
  echo "  or, knowingly: BACKUP_PLAINTEXT=1 make backup" >&2
  exit 2
fi
if [ -n "$recipient" ] && ! command -v age >/dev/null; then
  echo "refused: 'age' is not installed (macOS: brew install age; Debian/Ubuntu: apt install age)" >&2
  exit 2
fi
mkdir -p backups
chmod 700 backups
out="backups/agent-factory-$ts"
mkdir "$out"
cleanup() { if [ -n "$recipient" ]; then rm -rf "$out"; fi; }
trap cleanup EXIT

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

if [ -n "$recipient" ]; then
  tar -cf - -C "$out" . | age -r "$recipient" > "$out.tar.age"
  echo "backup written to $out.tar.age ($(du -sh "$out.tar.age" | cut -f1), encrypted with age)"
else
  echo "backup written to $out ($(du -sh "$out" | cut -f1)), NOT encrypted (BACKUP_PLAINTEXT=1)"
fi
