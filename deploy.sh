#!/bin/bash
# deploy.sh — Deploy treatwell-outreach to Ubuntu home server via Tailscale
set -euo pipefail

SERVER="mourad@100.96.142.127"
REMOTE_DIR="/opt/treatwell-outreach"
REPO="https://github.com/ilyas7mourad-art/treatwell-outreach.git"

echo "=== Deploying treatwell-outreach to $SERVER ==="

ssh "$SERVER" bash <<'REMOTE'
set -euo pipefail

REMOTE_DIR="/opt/treatwell-outreach"

# Clone or pull latest
if [ -d "$REMOTE_DIR/.git" ]; then
    echo "[*] Pulling latest..."
    git -C "$REMOTE_DIR" pull
else
    echo "[*] Cloning repo..."
    sudo git clone https://github.com/ilyas7mourad-art/treatwell-outreach.git "$REMOTE_DIR"
    sudo chown -R "$USER:$USER" "$REMOTE_DIR"
fi

cd "$REMOTE_DIR"

# Copy .env if it doesn't exist
if [ ! -f .env ]; then
    echo "[!] No .env file found — copying .env.example"
    cp .env.example .env
    echo "[!] Edit $REMOTE_DIR/.env before running the scraper."
fi

# Install Docker if missing
if ! command -v docker &>/dev/null; then
    echo "[*] Installing Docker..."
    curl -fsSL https://get.docker.com | sudo bash
    sudo usermod -aG docker "$USER"
fi

# Build and start
echo "[*] Building Docker image..."
docker compose build

echo ""
echo "=== Deploy complete! ==="
echo "Run scraper:      docker compose run --rm scraper"
echo "Or schedule:      crontab -e  (see README for cron entry)"
REMOTE
