#!/usr/bin/env bash
# XESXOR Bot — Amazon Linux 2023 provisioning script
# Run from the repo root:  bash deploy/setup_ec2.sh
set -euo pipefail

echo "[1/6] Installing system packages..."
sudo dnf install -y python3.11 python3.11-pip git

echo "[2/6] Creating 1G swap (t2.micro has 1G RAM)..."
if ! swapon --show | grep -q /swapfile; then
  sudo dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
  sudo chmod 600 /swapfile
  sudo mkswap -q /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
fi

echo "[3/6] Preparing /opt/xesxor-bot..."
sudo mkdir -p /opt/xesxor-bot
sudo chown ec2-user:ec2-user /opt/xesxor-bot

echo "[4/6] Python virtual environment..."
python3.11 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

echo "[5/6] Checking .env..."
test -f .env || { echo "ERROR: copy .env to /opt/xesxor-bot first (scp). See .env.example."; exit 1; }

echo "[6/6] Installing systemd service..."
sudo cp deploy/xesxor-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable xesxor-bot
echo "Done. Start with:  sudo systemctl start xesxor-bot"
