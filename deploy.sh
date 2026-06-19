#!/bin/bash
# deploy.sh — Deploy treatwell-outreach to Ubuntu home server (Tailscale)
# Usage: ./deploy.sh
# Idempotent — safe to re-run for updates.
set -euo pipefail

SERVER="mourad@100.96.142.127"
REMOTE_DIR="/home/mourad/treatwell-outreach"
REPO="https://github.com/ilyas7mourad-art/treatwell-outreach.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Preflight ────────────────────────────────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "ERROR: .env not found at $SCRIPT_DIR/.env — create it before deploying."
    exit 1
fi

echo "========================================================"
echo " Deploying treatwell-outreach → $SERVER:$REMOTE_DIR"
echo "========================================================"
echo ""

# ── 1. Clone or pull repo ────────────────────────────────────────────────────
echo "[1/6] Cloning / pulling repo..."
ssh "$SERVER" bash -s -- "$REMOTE_DIR" "$REPO" <<'EOF'
REMOTE_DIR="$1"; REPO="$2"
if [ -d "$REMOTE_DIR/.git" ]; then
    echo "  Pulling latest from GitHub..."
    git -C "$REMOTE_DIR" pull --ff-only
else
    echo "  Cloning repo..."
    git clone "$REPO" "$REMOTE_DIR"
fi
mkdir -p "$REMOTE_DIR/output" "$REMOTE_DIR/logs" "$REMOTE_DIR/raw_html"
EOF

# ── 2. Copy .env ─────────────────────────────────────────────────────────────
echo "[2/6] Copying .env to server..."
scp "$SCRIPT_DIR/.env" "$SERVER:$REMOTE_DIR/.env"
echo "  Done."

# ── 3. Install Python dependencies ───────────────────────────────────────────
echo "[3/6] Installing Python dependencies..."
ssh "$SERVER" bash -s -- "$REMOTE_DIR" <<'EOF'
REMOTE_DIR="$1"
pip3 install --break-system-packages -q -r "$REMOTE_DIR/requirements.txt"
echo "  pip install complete."
EOF

# ── 4. Install Playwright + Chromium ─────────────────────────────────────────
echo "[4/6] Installing Playwright Chromium..."
ssh "$SERVER" bash -s -- "$REMOTE_DIR" <<'EOF'
REMOTE_DIR="$1"
# Install Chromium browser for the current user
python3 -m playwright install chromium
# Install OS-level dependencies (needs sudo)
sudo python3 -m playwright install-deps chromium
echo "  Playwright Chromium ready."
EOF

# ── 5. Install systemd service ───────────────────────────────────────────────
echo "[5/6] Installing systemd service (treatwell-outreach)..."
ssh "$SERVER" bash -s -- "$REMOTE_DIR" <<'EOF'
REMOTE_DIR="$1"
chmod +x "$REMOTE_DIR/run_pipeline.sh"
sudo cp "$REMOTE_DIR/treatwell-outreach.service" /etc/systemd/system/treatwell-outreach.service
sudo systemctl daemon-reload
sudo systemctl enable treatwell-outreach
echo "  Service installed and enabled."
echo "  Start manually:  sudo systemctl start treatwell-outreach"
echo "  Check status:    sudo systemctl status treatwell-outreach"
EOF

# ── 6. Set up cron job (9am London time daily) ───────────────────────────────
echo "[6/6] Setting up cron job..."
ssh "$SERVER" bash -s -- "$REMOTE_DIR" <<'EOF'
REMOTE_DIR="$1"
CRON_JOB="0 9 * * * $REMOTE_DIR/run_pipeline.sh >> $REMOTE_DIR/logs/cron.log 2>&1"

# Remove any existing treatwell-outreach cron entry, then add fresh
( crontab -l 2>/dev/null | grep -v "treatwell-outreach\|run_pipeline" ; \
  echo "TZ=Europe/London" ; \
  echo "$CRON_JOB  # treatwell-outreach" ) | crontab -

echo "  Cron job set (9am Europe/London daily)."
echo "  Current crontab:"
crontab -l | grep -E "TZ|treatwell|run_pipeline" || true
EOF

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo " Deployment complete!"
echo "========================================================"
echo ""
echo "  SSH into server:    ssh $SERVER"
echo ""
echo "  Manual run:         sudo systemctl start treatwell-outreach"
echo "  Stop pipeline:      sudo systemctl stop treatwell-outreach"
echo "  Service status:     sudo systemctl status treatwell-outreach"
echo "  Live pipeline log:  tail -f $REMOTE_DIR/logs/pipeline.log"
echo "  Cron log:           tail -f $REMOTE_DIR/logs/cron.log"
echo ""
echo "  Cron: daily at 9am London time"
echo "  Scrapes: london + manchester, max 5 pages"
echo "  Daily send cap: 20 emails"
