from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from . import db
from .routing import sync_client_egress_routes


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check)


def _run_ok(*args: str) -> bool:
    return subprocess.run(args, text=True, capture_output=True).returncode == 0


def _iface_name(value: str) -> str:
    name = (value or "").strip()
    if not re.match(r"^[a-zA-Z0-9_.:-]{1,15}$", name):
        raise ValueError("invalid interface name")
    return name


def _region_code(value: str) -> str:
    code = re.sub(r"[^a-z0-9_-]+", "-", (value or "").strip().lower()).strip("-")
    if not code:
        raise ValueError("invalid region code")
    return code


def _ensure_config_path(iface_name: str) -> Path:
    return Path(f"/etc/amnezia/amneziawg/{iface_name}.conf")


def _ensure_service_name(iface_name: str) -> str:
    return f"amnezia-awg@{iface_name}.service"


def list_interfaces_text() -> str:
    rows = db.list_uplink_interfaces()
    if not rows:
        return "Interfaces list is empty."
    lines = ["Interfaces:"]
    for row in rows:
        lines.append(
            f"- {row['name']} | kind={row['kind']} | table_id={row['table_id']} | "
            f"enabled={row['enabled']} | config={row['config_path'] or '-'}"
        )
    return "\n".join(lines)


def list_regions_text() -> str:
    rows = db.list_regions()
    if not rows:
        return "Regions list is empty."
    lines = ["Regions:"]
    for row in rows:
        default_tag = " (default)" if int(row["is_default"]) else ""
        lines.append(f"- {row['label']} [{row['code']}] -> {row['interface_name']}{default_tag}")
    return "\n".join(lines)


def add_interface(name: str, kind: str = "amneziawg", table_id: int | None = None) -> None:
    iface = _iface_name(name)
    if kind not in ("amneziawg", "wireguard", "system"):
        raise ValueError("bad kind")

    cfg = None
    service = None
    tid = table_id
    if kind == "system":
        tid = None
    else:
        cfg = str(_ensure_config_path(iface))
        service = _ensure_service_name(iface)
        if tid is None:
            tid = db.next_table_id()
    db.upsert_uplink_interface(iface, kind, cfg, service, tid, enabled=1)
    sync_client_egress_routes()


def delete_interface(name: str) -> bool:
    iface = _iface_name(name)
    ok = db.delete_uplink_interface(iface)
    if ok:
        sync_client_egress_routes()
    return ok


def add_or_update_region(code: str, label: str, interface_name: str, is_default: bool = False) -> None:
    iface = _iface_name(interface_name)
    db.upsert_region(_region_code(code), label, iface, 1 if is_default else 0)
    sync_client_egress_routes()


def remove_region(code: str, move_to: str | None = None) -> bool:
    ok = db.delete_region(_region_code(code), _region_code(move_to) if move_to else None)
    if ok:
        sync_client_egress_routes()
    return ok


def set_default_region(code: str) -> bool:
    ok = db.set_default_region(_region_code(code))
    if ok:
        sync_client_egress_routes()
    return ok


def replace_interface_config(interface_name: str, config_text: str) -> tuple[bool, str]:
    iface = _iface_name(interface_name)
    row = db.get_uplink_interface(iface)
    if not row:
        return False, f"Interface {iface} not found in DB."
    if row["kind"] not in ("amneziawg", "wireguard"):
        return False, "Config replacement is only supported for VPN interfaces."
    if not config_text.strip():
        return False, "Config text is empty."

    config_path = Path(row["config_path"] or _ensure_config_path(iface))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    if config_path.exists():
        backup_path.write_text(config_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")

    config_path.write_text(config_text.strip() + "\n", encoding="utf-8")
    try:
        config_path.chmod(0o600)
    except Exception:
        pass

    try:
        if row["kind"] == "amneziawg":
            if not shutil.which("awg-quick"):
                raise RuntimeError("awg-quick is not installed")
            p = _run("awg-quick", "strip", iface, check=False)
            if p.returncode != 0:
                raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "awg-quick strip failed")
        else:
            if not shutil.which("wg-quick"):
                raise RuntimeError("wg-quick is not installed")
            p = _run("wg-quick", "strip", str(config_path), check=False)
            if p.returncode != 0:
                raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "wg-quick strip failed")

        if row["service_name"]:
            p = _run("systemctl", "restart", row["service_name"], check=False)
            if p.returncode != 0:
                raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "service restart failed")
        sync_client_egress_routes()
        return True, "Config applied successfully."
    except Exception as exc:
        if backup_path.exists():
            config_path.write_text(backup_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        if row["service_name"]:
            _run("systemctl", "restart", row["service_name"], check=False)
        sync_client_egress_routes()
        return False, f"Failed to apply config: {exc}"


def interface_status(name: str) -> tuple[bool, str]:
    iface = _iface_name(name)
    row = db.get_uplink_interface(iface)
    if not row:
        return False, f"{iface}: unknown interface."

    parts: list[str] = [f"{iface} ({row['kind']})"]
    ok = True

    if not _run_ok("ip", "link", "show", "dev", iface):
        return False, f"{iface}: link not found."

    if row["service_name"]:
        p = _run("systemctl", "is-active", row["service_name"], check=False)
        state = (p.stdout or "").strip()
        parts.append(f"service={state or 'unknown'}")
        if state != "active":
            ok = False

    if row["kind"] in ("amneziawg", "wireguard"):
        tool = "awg" if row["kind"] == "amneziawg" and shutil.which("awg") else "wg"
        p_hs = _run(tool, "show", iface, "latest-handshakes", check=False)
        hs_text = (p_hs.stdout or "").strip()
        if not hs_text:
            parts.append("handshake=none")
            ok = False
        else:
            latest = 0
            for line in hs_text.splitlines():
                cols = line.split()
                if len(cols) >= 2 and cols[1].isdigit():
                    latest = max(latest, int(cols[1]))
            if latest <= 0:
                parts.append("handshake=none")
                ok = False
            else:
                parts.append(f"handshake_unix={latest}")

    return ok, " | ".join(parts)
