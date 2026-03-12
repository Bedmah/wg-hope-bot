from __future__ import annotations

import os
from pathlib import Path

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

WG_INTERFACE = os.environ.get("WG_INTERFACE", "wg0")
WG_CONF = Path(os.environ.get("WG_CONF", f"/etc/wireguard/{WG_INTERFACE}.conf"))
SERVER_ENDPOINT = os.environ.get("SERVER_ENDPOINT", "")
SERVER_PUBLIC_KEY = os.environ.get("SERVER_PUBLIC_KEY", "")
VPN_SUBNET = os.environ.get("VPN_SUBNET", "10.8.0.0/24")
DNS_IP = os.environ.get("DNS_IP", "1.1.1.1")
KEEPALIVE = int(os.environ.get("KEEPALIVE", "25"))

BASE_DIR = Path(os.environ.get("PROJECT_DIR", Path(__file__).resolve().parents[1]))
CLIENTS_DIR = Path(os.environ.get("CLIENTS_DIR", BASE_DIR / "clients"))
CLIENTS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("DB_PATH", CLIENTS_DIR / "wg-bot.db"))
CHAT_DIR = Path(os.environ.get("CHAT_DIR", BASE_DIR / "chat"))
CHAT_DIR.mkdir(parents=True, exist_ok=True)

SUPER_OWNER_CHAT_ID = os.environ.get("SUPER_OWNER_CHAT_ID") or os.environ.get("ADMIN_CHAT_ID")
SUPPORT_HANDLE = os.environ.get("SUPPORT_HANDLE", "@support")

DEFAULT_USER_LIMIT = int(os.environ.get("DEFAULT_USER_LIMIT", "3"))
ADMIN_LIMIT = int(os.environ.get("ADMIN_LIMIT", "999"))
MONITOR_URL = os.environ.get("MONITOR_URL", "")
