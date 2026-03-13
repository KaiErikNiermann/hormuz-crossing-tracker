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

# 5. Create GitHub release with data assets
echo "--- Step 5: GitHub release ---"
python scripts/prepare_release.py

TAG="data-${REBUILD_DATE}"
DATES=$(python -c "import json; d=json.load(open('site/data/vessels_timeline.json')); print(len(d['dates']))")
VESSELS=$(python -c "import json; d=json.load(open('site/data/vessels_timeline.json')); print(len(d['vessels']))")
RANGE_START=$(python -c "import json; d=json.load(open('site/data/vessels_timeline.json')); print(d['date_range']['start'])")
RANGE_END=$(python -c "import json; d=json.load(open('site/data/vessels_timeline.json')); print(d['date_range']['end'])")

RELEASE_BODY=$(cat <<BODY
## Data Release ${REBUILD_DATE}

**Coverage**: ${RANGE_START} to ${RANGE_END} (${DATES} days, ${VESSELS} vessels)

### Assets
- \`vessels_timeline.json\` — Full cumulative GFW + AIS merged timeline
- \`gfw_timeline.json\` — GFW-only timeline
- \`vessels.json\` — Flat vessel metadata snapshot
- \`vessels_timeline_30d.json\` — Rolling 30-day batch
- \`DATA_LICENSE.md\` — Data licensing terms

### Data Sources & Licensing
- **Global Fishing Watch** — Vessel presence via 4Wings API. Licensed [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). Attribution: [globalfishingwatch.org](https://globalfishingwatch.org)
- **AIS** — Live vessel positions from public AIS broadcasts via [AISStream.io](https://aisstream.io)

> This data is provided for **non-commercial use only** per the GFW CC BY-NC 4.0 license.
BODY
)

gh release create "$TAG" \
  --title "Data snapshot ${REBUILD_DATE}" \
  --notes "$RELEASE_BODY" \
  release/vessels_timeline.json \
  release/gfw_timeline.json \
  release/vessels.json \
  release/vessels_timeline_30d.json \
  release/DATA_LICENSE.md \
  || echo "WARNING: GitHub release creation failed (non-fatal)"

rm -rf release/

echo "=== Rebuild completed at $(date -u --iso-8601=seconds) ==="
