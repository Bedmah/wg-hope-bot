from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone

from telegram.ext import ContextTypes

from . import db
from .routing import sync_client_egress_routes
from .server_admin import interface_status

HEALTHCHECK_INTERVAL_SEC = int(os.environ.get("UPLINK_HEALTHCHECK_INTERVAL_SEC", "60"))
HANDSHAKE_STALE_SEC = int(os.environ.get("UPLINK_HANDSHAKE_STALE_SEC", "60"))
DOWN_CONFIRM_COUNT = int(os.environ.get("UPLINK_DOWN_CONFIRM_COUNT", "2"))
UP_CONFIRM_COUNT = int(os.environ.get("UPLINK_UP_CONFIRM_COUNT", "2"))


def _is_handshake_stale(details: str) -> bool:
    marker = "handshake_unix="
    idx = details.find(marker)
    if idx < 0:
        return "handshake=none" in details
    raw = details[idx + len(marker) :].split("|", 1)[0].strip()
    if not raw.isdigit():
        return True
    ts = int(raw)
    now = int(datetime.now(timezone.utc).timestamp())
    return now - ts > HANDSHAKE_STALE_SEC


def _probe_connectivity(iface_name: str) -> bool:
    if not shutil.which("ping"):
        return False
    p = subprocess.run(
        ["ping", "-I", iface_name, "-c", "1", "-W", "2", "1.1.1.1"],
        text=True,
        capture_output=True,
    )
    return p.returncode == 0


async def run_uplink_healthcheck(context: ContextTypes.DEFAULT_TYPE) -> None:
    admins = db.admin_chat_ids()
    state_changed = False
    app = getattr(context, "application", None)
    bot_data = getattr(app, "bot_data", {}) if app else {}
    hc_state = bot_data.setdefault("uplink_hc_state", {})

    for iface in db.list_uplink_interfaces():
        if iface["kind"] == "system":
            continue
        ok, details = interface_status(iface["name"])
        if ok and _is_handshake_stale(details):
            if _probe_connectivity(iface["name"]):
                details = f"{details} | handshake_stale>{HANDSHAKE_STALE_SEC}s | probe=ok"
            else:
                ok = False
                details = f"{details} | handshake_stale>{HANDSHAKE_STALE_SEC}s | probe=fail"

        key = iface["name"]
        row = hc_state.get(key)
        if not row:
            prev = db.get_uplink_health(key)
            stable = prev["last_alert_state"] if prev and prev["last_alert_state"] in ("ok", "down") else "ok"
            row = {"stable": stable, "ok_streak": 0, "down_streak": 0}
            hc_state[key] = row

        if ok:
            row["ok_streak"] += 1
            row["down_streak"] = 0
        else:
            row["down_streak"] += 1
            row["ok_streak"] = 0

        state = row["stable"]
        should_notify = False
        if row["stable"] == "ok" and row["down_streak"] >= max(1, DOWN_CONFIRM_COUNT):
            state = "down"
            row["stable"] = "down"
            should_notify = True
            state_changed = True
        elif row["stable"] == "down" and row["ok_streak"] >= max(1, UP_CONFIRM_COUNT):
            state = "ok"
            row["stable"] = "ok"
            should_notify = True
            state_changed = True

        prev = db.get_uplink_health(iface["name"])
        db.set_uplink_health(iface["name"], ok, details, last_alert_state=state if should_notify else None)

        if not should_notify or not admins:
            continue

        if state == "down":
            text = f"[ALERT] Uplink {iface['name']} is DOWN\n{details}"
        else:
            text = f"[RECOVERY] Uplink {iface['name']} is OK\n{details}"

        for admin_id in admins:
            try:
                await context.bot.send_message(chat_id=int(admin_id), text=text)
            except Exception:
                pass

    if state_changed:
        try:
            sync_client_egress_routes()
        except Exception:
            pass
