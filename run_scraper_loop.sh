#!/bin/bash
# run_scraper_loop.sh — Continuous scrape + enrich loop (runs 24/7 as a daemon)
# Scrapes Treatwell, merges new venues into leads_master.csv, enriches missing data.
# Email sending is separate (daily cron via run_pipeline.sh).
set -euo pipefail

WORKDIR="/home/mma/treatwell-outreach"
MASTER="$WORKDIR/output/leads_master.csv"
SLEEP_SECONDS=300    # 5 min between runs (scraping takes hours so this is mostly a restart buffer)

cd "$WORKDIR"
mkdir -p "$WORKDIR/output" "$WORKDIR/logs"

LOOP_LOG="$WORKDIR/logs/scraper_loop.log"
exec > >(tee -a "$LOOP_LOG") 2>&1

echo "============================================================"
echo " Scraper loop started: $(date)"
echo " Sleep between runs: ${SLEEP_SECONDS}s"
echo "============================================================"

while true; do
    RUN_START=$(date +%Y%m%d_%H%M%S)
    echo ""
    echo "──── Run $RUN_START ────────────────────────────────────────"

    # ── Step 1: Scrape ───────────────────────────────────────────
    echo "[1/2] Scraping Treatwell..."
    LATEST_CSV="$WORKDIR/output/leads_${RUN_START}.csv"
    python3 -m scraper.main \
        --site all \
        --max-pages 50 \
        --no-raw-html \
        --log-level INFO

    # Find the CSV just written (newest non-enriched, non-master file)
    LATEST_CSV=$(ls -t "$WORKDIR/output"/leads_[0-9]*.csv 2>/dev/null \
                 | grep -v "_enriched\|_master" | head -1 || true)

    if [ -z "$LATEST_CSV" ]; then
        echo "  WARNING: No scraper output found — skipping this run."
    else
        # ── Step 1b: Merge into master (dedup by URL) ────────────
        echo "  Merging $LATEST_CSV → $MASTER"
        python3 -m scraper.merge \
            --new "$LATEST_CSV" \
            --master "$MASTER"

        # ── Step 2: Enrich master (skips already-enriched rows) ──
        if [ -f "$MASTER" ]; then
            echo "[2/2] Enriching master CSV..."
            python3 -m scraper.enrich \
                --input "$MASTER" \
                --resume \
                --log-level INFO
        fi
    fi

    echo "  Run complete: $(date). Sleeping ${SLEEP_SECONDS}s..."
    sleep "$SLEEP_SECONDS"
done
