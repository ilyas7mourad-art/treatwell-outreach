#!/bin/bash
# run_pipeline.sh — Daily Treatwell outreach pipeline
# Called by cron and by the treatwell-outreach systemd service.
# Scrape → Enrich → Send, all output logged with timestamps.
set -euo pipefail

WORKDIR="/home/mourad/treatwell-outreach"
cd "$WORKDIR"

mkdir -p "$WORKDIR/output" "$WORKDIR/logs"

LOG="$WORKDIR/logs/pipeline_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "============================================================"
echo " Treatwell pipeline started: $(date)"
echo "============================================================"

# ── Step 1: Scrape ──────────────────────────────────────────────
echo ""
echo "[1/3] Scraping Treatwell (london, manchester, max 5 pages)..."
python3 -m scraper.main \
    --site uk \
    --cities london manchester \
    --max-pages 5 \
    --no-raw-html \
    --log-level INFO

# Find the newest non-enriched leads CSV
LATEST_CSV=$(ls -t "$WORKDIR/output"/leads_[0-9]*.csv 2>/dev/null | grep -v "_enriched" | head -1 || true)
if [ -z "$LATEST_CSV" ]; then
    echo "ERROR: No leads CSV found after scraping. Aborting."
    exit 1
fi
echo "Scraper output: $LATEST_CSV"

# ── Step 2: Enrich ──────────────────────────────────────────────
echo ""
echo "[2/3] Enriching leads (Google Maps + email finder)..."
python3 -m scraper.enrich \
    --input "$LATEST_CSV" \
    --resume \
    --log-level INFO

ENRICHED_CSV="${LATEST_CSV%.csv}_enriched.csv"
if [ ! -f "$ENRICHED_CSV" ]; then
    echo "ERROR: Enriched CSV not found at $ENRICHED_CSV. Aborting."
    exit 1
fi
echo "Enriched CSV: $ENRICHED_CSV"

# ── Step 3: Send (initial + follow-ups, 20/day cap) ─────────────
echo ""
echo "[3/3] Sending emails and follow-ups (max 20 today)..."

# Run sender on every enriched CSV so follow-up sequences for older
# leads continue even when a new scrape produces a fresh CSV.
SENT_TODAY=0
for CSV in "$WORKDIR/output"/*_enriched.csv; do
    [ -f "$CSV" ] || continue
    echo "  Processing: $CSV"
    python3 -m scraper.send \
        --input "$CSV" \
        --max-daily 20 \
        --log-level INFO
done

echo ""
echo "============================================================"
echo " Pipeline completed: $(date)"
echo "============================================================"
