#!/usr/bin/env bash
set -euo pipefail

echo "[start] Python $(python --version)"
echo "[start] wg at $(which wg)"
echo "[start] ip at $(which ip)"
python -m src.main
