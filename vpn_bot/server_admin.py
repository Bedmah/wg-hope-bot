from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .routing import sync_client_egress_routes


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check)


def _run_ok(*args: str) -> bool:
    return subprocess.run(args, text=True, capture_output=True).returncode == 0


def _probe_connectivity(iface_name: str) -> bool:
    if not shutil.which("ping"):
        return False
    p = subprocess.run(
        ["ping", "-I", iface_name, "-c", "1", "-W", "2", "1.1.1.1"],
        text=True,
        capture_output=True,
    )
    return p.returncode == 0


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


def _force_table_off(config_text: str) -> str:
    """
    Ensure [Interface] contains `Table = off` so uplink configs with
    AllowedIPs=0.0.0.0/0 do not hijack system default routing.
    """
    lines = config_text.strip().splitlines()
    if not lines:
        return config_text

    out: list[str] = []
    in_interface = False
    saw_table = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_interface and not saw_table:
                out.append("Table = off")
            in_interface = stripped.lower() == "[interface]"
            saw_table = False
            out.append(line)
            continue

        if in_interface and re.match(r"(?i)^table\s*=", stripped):
            if not saw_table:
                out.append("Table = off")
                saw_table = True
            continue

        out.append(line)

    if in_interface and not saw_table:
        out.append("Table = off")

    return "\n".join(out).strip() + "\n"


def _config_has_table_off(config_text: str) -> bool:
    in_interface = False
    for raw in config_text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_interface = line.lower() == "[interface]"
            continue
        if not in_interface:
            continue
        m = re.match(r"(?i)^table\s*=\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().lower() == "off"
    return False


def _preflight_uplink_table_off(iface: str, kind: str, config_path: str | None) -> None:
    if kind not in ("amneziawg", "wireguard"):
        return
    path = Path(config_path or _ensure_config_path(iface))
    if not path.exists():
        raise ValueError(
            f"preflight failed: не найден конфиг {path}. "
            "Сначала добавь/замени конфиг интерфейса."
        )
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not _config_has_table_off(text):
        raise ValueError(
            f"preflight failed: в {path} должен быть 'Table = off' "
            "в секции [Interface]."
        )


def _ensure_stub_config_with_table_off(iface: str, kind: str, config_path: str | None) -> None:
    if kind not in ("amneziawg", "wireguard"):
        return
    path = Path(config_path or _ensure_config_path(iface))
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Minimal safe stub: keeps Table=off and lets admin replace config right after adding iface.
    path.write_text("[Interface]\nTable = off\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass


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


def sync_interface_services() -> None:
    for row in db.list_uplink_interfaces():
        service = (row["service_name"] or "").strip()
        if not service:
            continue
        enabled = int(row["enabled"]) == 1
        if enabled:
            _run("systemctl", "enable", service, check=False)
            _run("systemctl", "start", service, check=False)
        else:
            _run("systemctl", "stop", service, check=False)
            _run("systemctl", "disable", service, check=False)


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
        _ensure_stub_config_with_table_off(iface, kind, cfg)
        _preflight_uplink_table_off(iface, kind, cfg)
    db.upsert_uplink_interface(iface, kind, cfg, service, tid, enabled=1)
    if service:
        _run("systemctl", "enable", service, check=False)
    sync_client_egress_routes()


def delete_interface(name: str) -> bool:
    iface = _iface_name(name)
    row = db.get_uplink_interface(iface)
    ok = db.delete_uplink_interface(iface)
    if ok:
        if row and row["service_name"]:
            _run("systemctl", "stop", row["service_name"], check=False)
            _run("systemctl", "disable", row["service_name"], check=False)
        sync_client_egress_routes()
    return ok


def add_or_update_region(code: str, label: str, interface_name: str, is_default: bool = False) -> None:
    iface = _iface_name(interface_name)
    iface_row = db.get_uplink_interface(iface)
    if not iface_row:
        raise ValueError("interface not found")
    if int(iface_row["enabled"]) != 1:
        raise ValueError("interface is disabled")
    if iface_row["kind"] in ("amneziawg", "wireguard"):
        _preflight_uplink_table_off(iface, iface_row["kind"], iface_row["config_path"])
        ok, details = interface_status(iface)
        if not ok:
            raise ValueError(f"interface is not ready: {details}")
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

    normalized_text = config_text.strip() + "\n"
    if row["kind"] in ("amneziawg", "wireguard"):
        normalized_text = _force_table_off(normalized_text)

    config_path.write_text(normalized_text, encoding="utf-8")
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
            _run("systemctl", "enable", row["service_name"], check=False)
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
        stale_sec = int(os.environ.get("UPLINK_HANDSHAKE_STALE_SEC", "60"))
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
                now = int(datetime.now(timezone.utc).timestamp())
                age = now - latest
                if age > stale_sec:
                    if _probe_connectivity(iface):
                        parts.append(f"handshake_stale>{stale_sec}s")
                        parts.append("probe=ok")
                    else:
                        parts.append(f"handshake_stale>{stale_sec}s")
                        parts.append("probe=fail")
                        ok = False

    return ok, " | ".join(parts)
