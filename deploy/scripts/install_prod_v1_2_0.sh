#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Bedmah/wg-hope-bot.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/wg-hope-bot}"

echo "[1/7] Install base packages"
apt update
apt install -y python3 python3-venv python3-pip wireguard-tools iproute2 iptables conntrack curl git

echo "[2/7] Clone or update repo"
if [ -d "${INSTALL_DIR}/.git" ]; then
  git -C "${INSTALL_DIR}" fetch --all --tags
  git -C "${INSTALL_DIR}" pull --ff-only
else
  mkdir -p "${INSTALL_DIR}"
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

echo "[3/7] Setup virtualenv"
python3 -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "[4/7] Prepare directories"
mkdir -p "${INSTALL_DIR}/clients" "${INSTALL_DIR}/chat"

echo "[5/7] Prepare .env"
if [ ! -f "${INSTALL_DIR}/.env" ]; then
  cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
  echo "Created ${INSTALL_DIR}/.env from .env.example. Fill secrets before start."
fi

echo "[6/7] Install systemd units"
cp "${INSTALL_DIR}/deploy/systemd/wg-hope-bot.service" /etc/systemd/system/
cp "${INSTALL_DIR}/deploy/systemd/wg-hope-monitor.service" /etc/systemd/system/
systemctl daemon-reload

echo "[7/7] Enable services"
systemctl enable wg-hope-bot.service wg-hope-monitor.service
echo "Done. Start after editing .env:"
echo "  systemctl start wg-hope-bot.service wg-hope-monitor.service"
