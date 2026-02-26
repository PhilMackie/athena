#!/bin/bash
# Deploy Athena to Raspberry Pi
# Usage: ./deploy-to-pi.sh [pi@duobrain.local]

set -e

PI_HOST="${1:-pi@duobrain.local}"
APP_SRC="/home/phil/Dev/Claude/athena"
APP_DEST="/opt/athena/app"

echo "=== Deploying Athena to $PI_HOST ==="

echo "Creating remote directories..."
ssh "$PI_HOST" "sudo mkdir -p /opt/athena && sudo chown pi:pi /opt/athena && mkdir -p /opt/athena/app /opt/athena/logs"

echo "Syncing application files..."
rsync -avz --progress \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.git' \
  --exclude 'data/' \
  --exclude '.env' \
  "$APP_SRC/" \
  "$PI_HOST:$APP_DEST/"

echo "Syncing gunicorn config..."
rsync -avz "$APP_SRC/deploy/gunicorn.conf.py" "$PI_HOST:/opt/athena/"

if ssh "$PI_HOST" "test -f /opt/athena/venv/bin/pip"; then
    echo "Installing dependencies..."
    ssh "$PI_HOST" "/opt/athena/venv/bin/pip install -q -r $APP_DEST/requirements.txt"
    echo "Restarting service..."
    ssh "$PI_HOST" "sudo systemctl restart athena"
    echo ""
    ssh "$PI_HOST" "sudo systemctl status athena --no-pager | head -20"
else
    echo ""
    echo "Venv not set up yet. SSH in and run:"
    echo "  bash /opt/athena/app/deploy/pi-setup.sh"
fi

echo ""
echo "=== Done ==="
ssh "$PI_HOST" "sudo systemctl status athena --no-pager | head -20"
