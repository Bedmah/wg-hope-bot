from __future__ import annotations

import re
import shutil
import subprocess
from typing import Iterable

from . import db
from .settings import VPN_SUBNET, WG_INTERFACE

PRIO_BASE = 10000
PRIO_MAX = 13000
MANAGED_TABLE_PREFIX = "botif_"


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check)


def _run_ok(*args: str) -> bool:
    return subprocess.run(args, text=True, capture_output=True).returncode == 0


def _require_tools() -> bool:
    return bool(shutil.which("ip")) and bool(shutil.which("iptables"))


def _safe_iface_token(name: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower()
    return token or "if"


def table_name_for_iface(iface: str) -> str:
    return f"{MANAGED_TABLE_PREFIX}{_safe_iface_token(iface)}"


def _ensure_rt_table(table_id: int, table_name: str) -> None:
    path = "/etc/iproute2/rt_tables"
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    if re.search(rf"^\s*{table_id}\s+{re.escape(table_name)}\s*$", content, flags=re.M):
        return
    with open(path, "a", encoding="utf-8") as f:
        if not content.endswith("\n"):
            f.write("\n")
        f.write(f"{table_id} {table_name}\n")


def _default_main_gateway() -> tuple[str, str] | None:
    p = _run("ip", "route", "show", "default", check=False)
    if p.returncode != 0:
        return None
    line = next((x.strip() for x in p.stdout.splitlines() if x.strip()), "")
    if not line:
        return None
    m_via = re.search(r"\bvia\s+(\S+)", line)
    m_dev = re.search(r"\bdev\s+(\S+)", line)
    if not m_via or not m_dev:
        return None
    return m_via.group(1), m_dev.group(1)


def _endpoint_ip(iface: str) -> str | None:
    for tool in ("awg", "wg"):
        if not shutil.which(tool):
            continue
        p = _run(tool, "show", iface, "endpoints", check=False)
        if p.returncode != 0:
            continue
        for line in p.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            endpoint = parts[1]
            if endpoint == "(none)":
                continue
            m = re.match(r"^\[?([^\]]+)\]?:\d+$", endpoint)
            if m:
                return m.group(1)
    return None


def _host_from_ip(ip: str) -> int:
    try:
        return int(ip.rsplit(".", 1)[-1])
    except Exception:
        return 0


def _ensure_rule(source_ip: str, table_name: str, priority: int) -> None:
    _run_ok("ip", "-4", "rule", "add", "from", f"{source_ip}/32", "lookup", table_name, "priority", str(priority))


def _delete_managed_rules() -> None:
    p = _run("ip", "-4", "rule", "show", check=False)
    if p.returncode != 0:
        return
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        pref_str = line.split(":", 1)[0].strip()
        if not pref_str.isdigit():
            continue
        pref = int(pref_str)
        if pref < PRIO_BASE or pref >= PRIO_MAX:
            continue
        _run("ip", "-4", "rule", "del", "priority", str(pref), check=False)


def _ensure_iptables_rule(args: Iterable[str], table: str = "filter") -> None:
    if _run_ok("iptables", "-t", table, "-C", *args):
        return
    _run("iptables", "-t", table, "-A", *args, check=False)


def _delete_iptables_rule(args: Iterable[str], table: str = "filter") -> None:
    while _run_ok("iptables", "-t", table, "-C", *args):
        _run("iptables", "-t", table, "-D", *args, check=False)


def _configure_iface_table(iface_name: str, table_id: int) -> None:
    table_name = table_name_for_iface(iface_name)
    _ensure_rt_table(int(table_id), table_name)
    _run("ip", "route", "replace", "default", "dev", iface_name, "table", str(table_id), check=False)
    gw_info = _default_main_gateway()
    if not gw_info:
        return
    endpoint = _endpoint_ip(iface_name)
    if not endpoint:
        return
    gw, dev = gw_info
    _run("ip", "route", "replace", f"{endpoint}/32", "via", gw, "dev", dev, "table", str(table_id), check=False)


def _regions_map() -> dict[str, str]:
    result: dict[str, str] = {}
    for row in db.list_regions():
        result[row["code"]] = row["interface_name"]
    return result


def _interface_map() -> dict[str, dict]:
    return {row["name"]: dict(row) for row in db.list_uplink_interfaces()}


def _fallback_interface_name(regions: dict[str, str], interfaces: dict[str, dict]) -> str | None:
    preferred = regions.get("moscow")
    if preferred and preferred in interfaces:
        return preferred
    if "eth0" in interfaces:
        return "eth0"
    for name, row in interfaces.items():
        if row.get("kind") == "system":
            return name
    return next(iter(interfaces.keys()), None)


def _effective_regions_map() -> dict[str, str]:
    regions = _regions_map()
    interfaces = _interface_map()
    fallback_iface = _fallback_interface_name(regions, interfaces)
    if not fallback_iface:
        return regions

    effective: dict[str, str] = {}
    for region_code, iface_name in regions.items():
        iface = interfaces.get(iface_name)
        if not iface:
            effective[region_code] = fallback_iface
            continue
        if iface.get("kind") == "system":
            effective[region_code] = iface_name
            continue
        health = db.get_uplink_health(iface_name)
        iface_down = bool(health and int(health["is_ok"]) == 0)
        effective[region_code] = fallback_iface if iface_down else iface_name
    return effective


def _sync_rules_for_clients() -> None:
    _delete_managed_rules()
    regions = _effective_regions_map()
    interfaces = _interface_map()
    default_region = db.get_default_region_code()

    for row in db.list_all_clients():
        ip = (row["ip"] or "").strip()
        host = _host_from_ip(ip)
        if not ip or not host:
            continue

        region = (row["region"] or default_region).strip().lower()
        iface_name = regions.get(region) or regions.get(default_region)
        if not iface_name:
            continue
        iface = interfaces.get(iface_name)
        if not iface:
            continue
        table_id = iface.get("table_id")
        if table_id is None:
            continue
        table_name = table_name_for_iface(iface_name)
        _ensure_rule(ip, table_name, PRIO_BASE + host)


def _remove_legacy_rules() -> None:
    _delete_iptables_rule(
        ["PREROUTING", "-i", WG_INTERFACE, "-s", VPN_SUBNET, "!", "-d", VPN_SUBNET, "-j", "MARK", "--set-mark", "0x66"],
        table="mangle",
    )
    _run("ip", "-4", "rule", "del", "fwmark", "0x66", "table", "166", "priority", "1000", check=False)


def _sync_iptables() -> None:
    _remove_legacy_rules()

    regions = _effective_regions_map()
    interfaces = _interface_map()
    egress_ifaces = sorted({iface for iface in regions.values() if iface in interfaces})
    for iface_name in egress_ifaces:
        _ensure_iptables_rule(["POSTROUTING", "-s", VPN_SUBNET, "-o", iface_name, "-j", "MASQUERADE"], table="nat")
        _ensure_iptables_rule(["FORWARD", "-i", WG_INTERFACE, "-o", iface_name, "-s", VPN_SUBNET, "-j", "ACCEPT"])
        _ensure_iptables_rule(
            ["FORWARD", "-i", iface_name, "-o", WG_INTERFACE, "-d", VPN_SUBNET, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"]
        )


def sync_client_egress_routes() -> None:
    if not _require_tools():
        return
    for iface in db.list_uplink_interfaces():
        table_id = iface["table_id"]
        if table_id is None:
            continue
        _configure_iface_table(iface["name"], int(table_id))
    _sync_rules_for_clients()
    _sync_iptables()
