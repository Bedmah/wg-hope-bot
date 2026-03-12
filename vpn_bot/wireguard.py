from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path

from . import db
from .settings import WG_INTERFACE, WG_CONF, VPN_SUBNET, DNS_IP, SERVER_PUBLIC_KEY, SERVER_ENDPOINT, KEEPALIVE


def run(cmd: str) -> str:
    res = subprocess.run(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"command failed: {cmd}")
    return res.stdout.strip()


def _iface_exists() -> bool:
    p = subprocess.run(
        f"ip link show dev {WG_INTERFACE}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return p.returncode == 0


def ensure_iface_up() -> None:
    if _iface_exists():
        return
    run(f"wg-quick up {WG_INTERFACE}")


def _used_ips_from_conf() -> set[str]:
    used: set[str] = set()
    if not WG_CONF.exists():
        return used

    for line in WG_CONF.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.search(r"AllowedIPs\s*=\s*([\d\.]+)/\d+", line)
        if m:
            used.add(m.group(1))
    return used


def allocate_ip() -> str:
    net = ipaddress.ip_network(VPN_SUBNET, strict=False)
    used = _used_ips_from_conf() | db.used_ips_from_db()
    first_host = next(net.hosts(), None)
    if first_host:
        used.add(str(first_host))  # reserve .1 for server

    for host in net.hosts():
        ip = str(host)
        if ip.endswith(".1"):
            continue
        if ip not in used:
            return ip
    raise RuntimeError("Свободные IP в подсети закончились")


def validate_ip(ip_str: str) -> None:
    net = ipaddress.ip_network(VPN_SUBNET, strict=False)
    ip = ipaddress.ip_address(ip_str)
    if ip not in net:
        raise RuntimeError(f"IP {ip_str} вне подсети {VPN_SUBNET}")
    if ip_str in (_used_ips_from_conf() | db.used_ips_from_db()):
        raise RuntimeError(f"IP {ip_str} уже занят")


def add_peer(pub_key: str, client_ip: str) -> None:
    ensure_iface_up()
    run(f"wg set {WG_INTERFACE} peer {pub_key} allowed-ips {client_ip}/32")
    block = f"\n[Peer]\nPublicKey = {pub_key}\nAllowedIPs = {client_ip}/32\n"
    old = WG_CONF.read_text(encoding="utf-8", errors="ignore") if WG_CONF.exists() else ""
    WG_CONF.write_text(old + block, encoding="utf-8")


def remove_peer(pub_key: str) -> None:
    if not _iface_exists():
        return
    try:
        run(f"wg set {WG_INTERFACE} peer {pub_key} remove")
    except Exception:
        pass


def remove_peer_block(pub_key: str) -> None:
    if not WG_CONF.exists():
        return
    data = WG_CONF.read_text(encoding="utf-8", errors="ignore")
    # remove [Peer] block containing matching PublicKey
    pattern = r"\n\[Peer\][^\[]*?PublicKey\s*=\s*" + re.escape(pub_key) + r"[^\[]*?(?=\n\[Peer\]|\Z)"
    data = re.sub(pattern, "\n", data, flags=re.S)
    WG_CONF.write_text(data, encoding="utf-8")


def build_client_config(private_key: str, client_ip: str) -> str:
    return (
        "[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {client_ip}/32\n"
        f"DNS = {DNS_IP}\n\n"
        "[Peer]\n"
        f"PublicKey = {SERVER_PUBLIC_KEY}\n"
        f"Endpoint = {SERVER_ENDPOINT}\n"
        "AllowedIPs = 0.0.0.0/0\n"
        f"PersistentKeepalive = {KEEPALIVE}\n"
    )
