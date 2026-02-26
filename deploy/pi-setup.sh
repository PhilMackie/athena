#!/bin/bash
# One-time Athena setup on the Pi.
# Run ON THE PI: bash /opt/athena/app/deploy/pi-setup.sh
# (Pi already has Python, venv support, UFW from duo-brain setup.)

set -e

echo "=== Athena Pi Setup ==="

# Directories
sudo mkdir -p /opt/athena
sudo chown pi:pi /opt/athena
mkdir -p /opt/athena/app /opt/athena/logs

# Virtual environment
echo "Creating venv..."
python3 -m venv /opt/athena/venv
/opt/athena/venv/bin/pip install --upgrade pip -q

# .env
if [ ! -f /opt/athena/app/.env ]; then
    echo ""
    echo "No .env found — creating one."
    echo "Enter PIN for Athena (same as Quanta is fine):"
    read -s PIN
    PIN_HASH=$(python3 -c "import hashlib; print(hashlib.sha256('$PIN'.encode()).hexdigest())")
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    cat > /opt/athena/app/.env << EOF
SECRET_KEY=$SECRET_KEY
AUTH_ENABLED=true
PIN_HASH=$PIN_HASH
OBSIDIAN_VAULT=/opt/duobrain/vault
EOF
    chmod 600 /opt/athena/app/.env
    echo ".env created."
fi

# Open firewall port
echo "Opening port 5002..."
sudo ufw allow 5002/tcp

# Systemd service
echo "Installing systemd service..."
sudo cp /opt/athena/app/deploy/athena.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable athena
sudo systemctl start athena

echo ""
sleep 2
sudo systemctl status athena --no-pager
echo ""
echo "=== Athena is running on port 5002 ==="
echo "Test: curl -I http://localhost:5002/login"
