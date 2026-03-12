#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  source ./.env
  set +a
fi

echo "[start] Python $(python --version)"

if [ -x ./.venv/bin/python ]; then
  exec ./.venv/bin/python -m vpn_bot.main
fi

exec python -m vpn_bot.main
