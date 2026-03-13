#!/usr/bin/env bash
# Local scheduled rebuild: GFW fetch + AIS merge + frontend build + commit + push
# Intended to run via cron every 5 days. Pre-push hook validates before push.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

LOG_FILE="$PROJECT_DIR/logs/rebuild-$(date -u +%Y%m%d-%H%M%S).log"
mkdir -p "$PROJECT_DIR/logs"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Rebuild started at $(date -u --iso-8601=seconds) ==="

# 1. GFW fetch (accumulates into gfw_timeline.json)
echo "--- Step 1: GFW fetch ---"
poetry run fetch-gfw

# 2. Merge GFW + live AIS from PostgreSQL
echo "--- Step 2: Export merge (GFW + AIS) ---"
export HORMUZ_DB_HOST=localhost
export HORMUZ_DB_PORT=5433
export HORMUZ_DB_NAME=hormuz
export HORMUZ_DB_USER=hormuz
export HORMUZ_DB_PASSWORD=hormuz
poetry run export-snapshot

# 3. Build frontend
echo "--- Step 3: Frontend build ---"
cd site
pnpm run build
cd ..

# 4. Commit and push (pre-push hook will validate)
echo "--- Step 4: Commit and push ---"
REBUILD_DATE="$(date -u +%Y-%m-%d)"

git add site/data/gfw_timeline.json site/data/vessels_timeline.json site/data/vessels.json site/dist/
git diff --cached --quiet && { echo "No changes to commit — skipping push"; exit 0; }

git commit -m "chore(data): scheduled rebuild ${REBUILD_DATE}

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

# Push — pre-push hook runs here and will block if checks fail
git push

echo "=== Rebuild completed at $(date -u --iso-8601=seconds) ==="
