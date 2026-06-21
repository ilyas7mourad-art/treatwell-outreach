#!/bin/bash
# run_pipeline.sh — Daily outreach: email sends + Google Sheets sync
set -euo pipefail

WORKDIR="/home/mma/treatwell-outreach"
MASTER="$WORKDIR/output/leads_master_enriched.csv"

cd "$WORKDIR"
mkdir -p "$WORKDIR/output" "$WORKDIR/logs"

LOG="$WORKDIR/logs/pipeline_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "============================================================"
echo " Daily pipeline started: $(date)"
echo "============================================================"

if [ ! -f "$MASTER" ]; then
    echo "No master CSV yet — scraper loop hasn't run yet."
    exit 0
fi

echo "[1/2] Sending emails..."
python3 -m scraper.send \
    --input "$MASTER" \
    --skip-reply-check \
    --log-level INFO || echo "WARNING: Email send failed (non-fatal)"

echo ""
echo "[2/2] Syncing to Google Sheets..."
python3 -m scraper.sync_sheets || echo "WARNING: Sheets sync failed (non-fatal)"

echo ""
echo "============================================================"
echo " Daily pipeline complete: $(date)"
echo "============================================================"
