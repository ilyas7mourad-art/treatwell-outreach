#!/bin/bash
# run_pipeline.sh — Daily email send step (runs at 9am London time via cron)
# Reads from leads_master.csv which the scraper loop keeps up to date.
set -euo pipefail

WORKDIR="/home/mma/treatwell-outreach"
MASTER="$WORKDIR/output/leads_master.csv"

cd "$WORKDIR"
mkdir -p "$WORKDIR/output" "$WORKDIR/logs"

LOG="$WORKDIR/logs/pipeline_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "============================================================"
echo " Daily send started: $(date)"
echo "============================================================"

if [ ! -f "$MASTER" ]; then
    echo "No master CSV yet ($MASTER). Scraper loop hasn't run yet — try again later."
    exit 0
fi

echo "Sending emails from: $MASTER"
python3 -m scraper.send \
    --input "$MASTER" \
    --max-daily 20 \
    --log-level INFO

echo ""
echo "============================================================"
echo " Daily send complete: $(date)"
echo "============================================================"
