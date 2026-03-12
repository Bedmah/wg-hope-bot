from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import socket
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from vpn_bot.settings import CHAT_DIR, DB_PATH, WG_INTERFACE

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
WEB_SECRET = os.environ.get("WEB_SECRET", "change-me-monitor-secret")
MONITOR_POLL_SEC = int(os.environ.get("MONITOR_POLL_SEC", "30"))
MONITOR_KEEP_DAYS = int(os.environ.get("MONITOR_KEEP_DAYS", "90"))
DNS_CACHE_DAYS = int(os.environ.get("MONITOR_DNS_CACHE_DAYS", "7"))

SERVICE_BY_PORT = {
    53: "DNS",
    80: "HTTP",
    123: "NTP",
    443: "HTTPS",
    465: "SMTPS",
    587: "SMTP",
    993: "IMAPS",
    995: "POP3S",
    3478: "STUN/TURN",
    5222: "XMPP",
}

app = FastAPI(title="WG Hope Monitor")
app.add_middleware(SessionMiddleware, secret_key=WEB_SECRET)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_to_iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt_hex = salt_hex or secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 200_000).hex()
    return salt_hex, hashed


def verify_password(password: str, salt_hex: str, expected_hash: str) -> bool:
    _, got = hash_password(password, salt_hex)
    return secrets.compare_digest(got, expected_hash)


def init_monitor_db() -> None:
    with db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monitor_auth(
                id INTEGER PRIMARY KEY CHECK(id=1),
                username TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wg_peer_samples(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                peer_pub TEXT NOT NULL,
                endpoint TEXT,
                allowed_ips TEXT,
                latest_handshake INTEGER NOT NULL,
                rx INTEGER NOT NULL,
                tx INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wg_peer_samples_pub_id ON wg_peer_samples(peer_pub, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wg_peer_samples_ts ON wg_peer_samples(ts)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wg_peer_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_ts TEXT NOT NULL,
                peer_pub TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wg_peer_events_pub_ts ON wg_peer_events(peer_pub, event_ts)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wg_peer_state(
                peer_pub TEXT PRIMARY KEY,
                last_seen_ts TEXT NOT NULL,
                last_endpoint TEXT,
                last_handshake INTEGER NOT NULL,
                last_rx INTEGER NOT NULL,
                last_tx INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS net_destinations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                owner_chat_id TEXT NOT NULL,
                client_ip TEXT NOT NULL,
                peer_pub TEXT NOT NULL,
                config_name TEXT NOT NULL,
                dst_ip TEXT NOT NULL,
                dst_port INTEGER NOT NULL,
                proto TEXT NOT NULL,
                state TEXT,
                service TEXT,
                domain TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_net_destinations_owner_ts ON net_destinations(owner_chat_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_net_destinations_peer_ts ON net_destinations(peer_pub, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_net_destinations_dst_ts ON net_destinations(dst_ip, ts)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dns_cache(
                ip TEXT PRIMARY KEY,
                domain TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )

        auth = conn.execute("SELECT id FROM monitor_auth WHERE id=1").fetchone()
        if not auth:
            salt, hashed = hash_password("admin")
            conn.execute(
                "INSERT INTO monitor_auth(id, username, password_salt, password_hash, updated_at) VALUES(1, ?, ?, ?, ?)",
                ("admin", salt, hashed, now_iso()),
            )
        conn.commit()


def verify_credentials(username: str, password: str) -> bool:
    with db_conn() as conn:
        row = conn.execute("SELECT username, password_salt, password_hash FROM monitor_auth WHERE id=1").fetchone()
        if not row:
            return False
        return row["username"] == username and verify_password(password, row["password_salt"], row["password_hash"])


def change_credentials(current_password: str, new_username: str, new_password: str) -> tuple[bool, str]:
    with db_conn() as conn:
        row = conn.execute("SELECT username, password_salt, password_hash FROM monitor_auth WHERE id=1").fetchone()
        if not row:
            return False, "Профиль администратора не найден."
        if not verify_password(current_password, row["password_salt"], row["password_hash"]):
            return False, "Текущий пароль неверный."
        if len(new_username.strip()) < 3:
            return False, "Логин слишком короткий."
        if len(new_password) < 6:
            return False, "Пароль слишком короткий (минимум 6 символов)."
        salt, hashed = hash_password(new_password)
        conn.execute(
            "UPDATE monitor_auth SET username=?, password_salt=?, password_hash=?, updated_at=? WHERE id=1",
            (new_username.strip(), salt, hashed, now_iso()),
        )
        conn.commit()
    return True, "Учетные данные обновлены."


def run_wg_dump() -> list[dict[str, Any]]:
    proc = subprocess.run(
        ["wg", "show", WG_INTERFACE, "dump"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "wg show failed")
    lines = [x.strip() for x in proc.stdout.splitlines() if x.strip()]
    peers: list[dict[str, Any]] = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        peer_pub, _, endpoint, allowed_ips, latest_handshake, rx, tx, _ = parts[:8]
        peers.append(
            {
                "peer_pub": peer_pub,
                "endpoint": endpoint or "",
                "allowed_ips": allowed_ips or "",
                "latest_handshake": int(latest_handshake or "0"),
                "rx": int(rx or "0"),
                "tx": int(tx or "0"),
            }
        )
    return peers


def resolve_domain(conn: sqlite3.Connection, ip: str) -> str:
    ttl_before = (datetime.now(timezone.utc) - timedelta(days=DNS_CACHE_DAYS)).isoformat().replace("+00:00", "Z")
    row = conn.execute("SELECT domain, updated_at FROM dns_cache WHERE ip=?", (ip,)).fetchone()
    if row and row["updated_at"] >= ttl_before:
        return row["domain"] or ""
    domain = ""
    try:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(0.6)
        domain = socket.gethostbyaddr(ip)[0]
        socket.setdefaulttimeout(old_timeout)
    except Exception:
        domain = ""
    conn.execute(
        """
        INSERT INTO dns_cache(ip, domain, updated_at) VALUES(?,?,?)
        ON CONFLICT(ip) DO UPDATE SET domain=excluded.domain, updated_at=excluded.updated_at
        """,
        (ip, domain, now_iso()),
    )
    return domain


def parse_conntrack_line(line: str) -> dict[str, str] | None:
    parts = line.strip().split()
    if len(parts) < 4:
        return None
    proto = parts[0].lower()
    state = parts[3] if "=" not in parts[3] else ""
    pairs = re.findall(r"([a-zA-Z_]+)=([^\s]+)", line)
    data: dict[str, list[str]] = {}
    for k, v in pairs:
        data.setdefault(k, []).append(v)
    src = data.get("src", [""])[0]
    dst = data.get("dst", [""])[0]
    dport = data.get("dport", ["0"])[0]
    if not src or not dst:
        return None
    return {
        "proto": proto,
        "state": state,
        "src": src,
        "dst": dst,
        "dport": dport,
    }


def collect_destinations(conn: sqlite3.Connection, ts: str) -> None:
    client_rows = conn.execute("SELECT owner_chat_id, name, ip, pub FROM clients").fetchall()
    ip_map = {r["ip"]: r for r in client_rows}
    if not ip_map:
        return

    proc = subprocess.run(
        ["conntrack", "-L"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return

    seen: set[tuple[str, str, str, str, int]] = set()
    unknown_dns_budget = 20
    for line in proc.stdout.splitlines():
        parsed = parse_conntrack_line(line)
        if not parsed:
            continue
        src = parsed["src"]
        if src not in ip_map:
            continue
        dst_ip = parsed["dst"]
        try:
            dport = int(parsed["dport"])
        except Exception:
            dport = 0
        key = (src, dst_ip, parsed["proto"], parsed["state"], dport)
        if key in seen:
            continue
        seen.add(key)

        client = ip_map[src]
        service = SERVICE_BY_PORT.get(dport, "other")
        domain = ""
        if unknown_dns_budget > 0:
            domain = resolve_domain(conn, dst_ip)
            if not domain:
                unknown_dns_budget -= 1

        conn.execute(
            """
            INSERT INTO net_destinations(
              ts, owner_chat_id, client_ip, peer_pub, config_name, dst_ip, dst_port, proto, state, service, domain
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                client["owner_chat_id"],
                src,
                client["pub"],
                client["name"],
                dst_ip,
                dport,
                parsed["proto"],
                parsed["state"],
                service,
                domain,
            ),
        )


def collect_once() -> None:
    peers = run_wg_dump()
    ts = now_iso()
    keep_before = (datetime.now(timezone.utc) - timedelta(days=MONITOR_KEEP_DAYS)).isoformat().replace("+00:00", "Z")

    with db_conn() as conn:
        for peer in peers:
            conn.execute(
                """
                INSERT INTO wg_peer_samples(ts, peer_pub, endpoint, allowed_ips, latest_handshake, rx, tx)
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    ts,
                    peer["peer_pub"],
                    peer["endpoint"],
                    peer["allowed_ips"],
                    peer["latest_handshake"],
                    peer["rx"],
                    peer["tx"],
                ),
            )
            prev = conn.execute(
                "SELECT last_endpoint, last_handshake, last_rx, last_tx FROM wg_peer_state WHERE peer_pub=?",
                (peer["peer_pub"],),
            ).fetchone()
            if prev:
                if peer["latest_handshake"] > 0 and peer["latest_handshake"] != int(prev["last_handshake"]):
                    conn.execute(
                        "INSERT INTO wg_peer_events(event_ts, peer_pub, event_type, details) VALUES(?,?,?,?)",
                        (epoch_to_iso(peer["latest_handshake"]), peer["peer_pub"], "handshake", f"endpoint={peer['endpoint']}"),
                    )
                if (prev["last_endpoint"] or "") != (peer["endpoint"] or ""):
                    conn.execute(
                        "INSERT INTO wg_peer_events(event_ts, peer_pub, event_type, details) VALUES(?,?,?,?)",
                        (ts, peer["peer_pub"], "endpoint_change", f"{prev['last_endpoint']} -> {peer['endpoint']}"),
                    )
                delta_rx = peer["rx"] - int(prev["last_rx"])
                delta_tx = peer["tx"] - int(prev["last_tx"])
                if delta_rx < 0:
                    delta_rx = peer["rx"]
                if delta_tx < 0:
                    delta_tx = peer["tx"]
                if delta_rx > 0 or delta_tx > 0:
                    conn.execute(
                        "INSERT INTO wg_peer_events(event_ts, peer_pub, event_type, details) VALUES(?,?,?,?)",
                        (ts, peer["peer_pub"], "traffic", f"delta_rx={delta_rx} delta_tx={delta_tx}"),
                    )

            conn.execute(
                """
                INSERT INTO wg_peer_state(peer_pub, last_seen_ts, last_endpoint, last_handshake, last_rx, last_tx)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(peer_pub) DO UPDATE SET
                  last_seen_ts=excluded.last_seen_ts,
                  last_endpoint=excluded.last_endpoint,
                  last_handshake=excluded.last_handshake,
                  last_rx=excluded.last_rx,
                  last_tx=excluded.last_tx
                """,
                (
                    peer["peer_pub"],
                    ts,
                    peer["endpoint"],
                    peer["latest_handshake"],
                    peer["rx"],
                    peer["tx"],
                ),
            )

        conn.execute("DELETE FROM wg_peer_samples WHERE ts < ?", (keep_before,))
        conn.execute("DELETE FROM wg_peer_events WHERE event_ts < ?", (keep_before,))
        conn.commit()


def human_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    num = float(max(0, value))
    for unit in units:
        if num < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.2f}".replace(".", ",") + f" {unit}"
        num /= 1024
    return f"{int(value)} B"


def bytes_to_gb(value: int) -> float:
    return float(max(value, 0)) / (1024 ** 3)


def fmt2(value: float) -> str:
    return f"{float(value):.2f}".replace(".", ",")


def is_auth(request: Request) -> bool:
    return bool(request.session.get("auth_user"))


def redirect_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


def get_latest_samples(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT s.peer_pub, s.endpoint, s.latest_handshake, s.rx, s.tx, s.ts
        FROM wg_peer_samples s
        JOIN (SELECT peer_pub, MAX(id) AS max_id FROM wg_peer_samples GROUP BY peer_pub) x ON x.max_id = s.id
        """
    ).fetchall()
    return {r["peer_pub"]: r for r in rows}


def handshake_count(conn: sqlite3.Connection, peer_pub: str, hours: int) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM wg_peer_events WHERE peer_pub=? AND event_type='handshake' AND event_ts>=?",
        (peer_pub, since),
    ).fetchone()
    return int(row["n"]) if row else 0


def traffic_delta(conn: sqlite3.Connection, peer_pub: str, hours: int) -> tuple[int, int]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    first = conn.execute(
        "SELECT rx, tx FROM wg_peer_samples WHERE peer_pub=? AND ts>=? ORDER BY id ASC LIMIT 1",
        (peer_pub, since),
    ).fetchone()
    last = conn.execute(
        "SELECT rx, tx FROM wg_peer_samples WHERE peer_pub=? ORDER BY id DESC LIMIT 1",
        (peer_pub,),
    ).fetchone()
    if not first or not last:
        return 0, 0
    rx = int(last["rx"]) - int(first["rx"])
    tx = int(last["tx"]) - int(first["tx"])
    return (int(last["rx"]) if rx < 0 else rx, int(last["tx"]) if tx < 0 else tx)


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))


def make_labels(start: datetime, bucket_seconds: int, count: int) -> list[str]:
    labels: list[str] = []
    for i in range(count):
        dt = start + timedelta(seconds=i * bucket_seconds)
        if bucket_seconds <= 3600:
            labels.append(dt.strftime("%H:%M"))
        elif bucket_seconds <= 6 * 3600:
            labels.append(dt.strftime("%d %H:%M"))
        else:
            labels.append(dt.strftime("%d.%m"))
    return labels


def bucket_label_ru(bucket_seconds: int) -> str:
    if bucket_seconds < 60:
        return f"{bucket_seconds} сек"
    if bucket_seconds < 3600:
        return f"{bucket_seconds // 60} мин"
    if bucket_seconds < 86400:
        return f"{bucket_seconds // 3600} ч"
    return f"{bucket_seconds // 86400} дн"


def period_params(conn: sqlite3.Connection, period: str, peer_pubs: list[str]) -> tuple[datetime, int, int, str]:
    now = datetime.now(timezone.utc)
    if period == "1h":
        start = now - timedelta(hours=1)
        bucket = 300  # 5 min
    elif period == "7d":
        start = now - timedelta(days=7)
        bucket = 6 * 3600
    elif period == "30d":
        start = now - timedelta(days=30)
        bucket = 24 * 3600
    elif period == "all":
        if peer_pubs:
            ph = ",".join("?" for _ in peer_pubs)
            row = conn.execute(
                f"SELECT MIN(ts) AS ts FROM wg_peer_samples WHERE peer_pub IN ({ph})",
                tuple(peer_pubs),
            ).fetchone()
        else:
            row = conn.execute("SELECT MIN(ts) AS ts FROM wg_peer_samples").fetchone()
        if row and row["ts"]:
            start = parse_iso(row["ts"])
        else:
            start = now - timedelta(days=1)
        bucket = 24 * 3600
    else:
        start = now - timedelta(hours=24)
        bucket = 3600

    total_seconds = max(int((now - start).total_seconds()), bucket)
    count = max(min((total_seconds // bucket) + 1, 400), 1)
    start = now - timedelta(seconds=(count - 1) * bucket)
    return start, bucket, count, period


def peer_series(
    conn: sqlite3.Connection,
    peer_pub: str,
    start: datetime,
    bucket_seconds: int,
    bucket_count: int,
) -> tuple[list[int], list[int]]:
    start_iso = start.isoformat().replace("+00:00", "Z")
    traffic = [0] * bucket_count
    handshakes = [0] * bucket_count

    prev = conn.execute(
        "SELECT rx, tx FROM wg_peer_samples WHERE peer_pub=? AND ts<? ORDER BY id DESC LIMIT 1",
        (peer_pub, start_iso),
    ).fetchone()
    prev_rx = int(prev["rx"]) if prev else None
    prev_tx = int(prev["tx"]) if prev else None

    rows = conn.execute(
        "SELECT ts, rx, tx FROM wg_peer_samples WHERE peer_pub=? AND ts>=? ORDER BY id ASC",
        (peer_pub, start_iso),
    ).fetchall()
    for r in rows:
        try:
            dt = parse_iso(r["ts"])
        except Exception:
            continue
        idx = int((dt - start).total_seconds() // bucket_seconds)
        if idx < 0 or idx >= bucket_count:
            prev_rx = int(r["rx"])
            prev_tx = int(r["tx"])
            continue
        rx = int(r["rx"])
        tx = int(r["tx"])
        if prev_rx is not None and prev_tx is not None:
            d_rx = rx - prev_rx
            d_tx = tx - prev_tx
            if d_rx < 0:
                d_rx = rx
            if d_tx < 0:
                d_tx = tx
            traffic[idx] += max(d_rx + d_tx, 0)
        prev_rx = rx
        prev_tx = tx

    h_rows = conn.execute(
        "SELECT event_ts FROM wg_peer_events WHERE peer_pub=? AND event_type='handshake' AND event_ts>=?",
        (peer_pub, start_iso),
    ).fetchall()
    for r in h_rows:
        try:
            dt = parse_iso(r["event_ts"])
        except Exception:
            continue
        idx = int((dt - start).total_seconds() // bucket_seconds)
        if 0 <= idx < bucket_count:
            handshakes[idx] += 1

    return traffic, handshakes


def top_destinations_for_owner(conn: sqlite3.Connection, owner_chat_id: str, hours: int = 24, limit: int = 3) -> list[str]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(domain,''), dst_ip) AS label, COUNT(*) AS n
        FROM net_destinations
        WHERE owner_chat_id=? AND ts>=?
        GROUP BY label
        ORDER BY n DESC
        LIMIT ?
        """,
        (owner_chat_id, since, limit),
    ).fetchall()
    return [f"{r['label']} ({r['n']})" for r in rows]


def load_dashboard() -> list[dict[str, Any]]:
    with db_conn() as conn:
        users = conn.execute(
            "SELECT chat_id, role, username, first_name, last_name, created_at, last_seen, max_clients FROM users ORDER BY created_at"
        ).fetchall()
        clients = conn.execute("SELECT owner_chat_id, name, ip, pub, created_at FROM clients ORDER BY created_at DESC").fetchall()
        by_owner: dict[str, list[sqlite3.Row]] = {}
        for c in clients:
            by_owner.setdefault(c["owner_chat_id"], []).append(c)
        latest = get_latest_samples(conn)

        out: list[dict[str, Any]] = []
        for u in users:
            chat_id = u["chat_id"]
            rows = by_owner.get(chat_id, [])
            total_rx, total_tx, h24, h7d, last_hs, active24 = 0, 0, 0, 0, 0, 0
            for c in rows:
                sample = latest.get(c["pub"])
                if sample:
                    total_rx += int(sample["rx"])
                    total_tx += int(sample["tx"])
                    last_hs = max(last_hs, int(sample["latest_handshake"]))
                h24_peer = handshake_count(conn, c["pub"], 24)
                h24 += h24_peer
                h7d += handshake_count(conn, c["pub"], 24 * 7)
                if h24_peer > 0:
                    active24 += 1
            out.append(
                {
                    "chat_id": chat_id,
                    "role": u["role"],
                    "username": u["username"] or "",
                    "full_name": " ".join(x for x in [u["first_name"], u["last_name"]] if x) or "",
                    "clients_count": len(rows),
                    "active_configs_24h": active24,
                    "handshakes_24h": h24,
                    "handshakes_7d": h7d,
                    "total_rx": human_bytes(total_rx),
                    "total_tx": human_bytes(total_tx),
                    "last_handshake": epoch_to_iso(last_hs) if last_hs > 0 else "-",
                    "last_seen": u["last_seen"] or "-",
                    "top_destinations": ", ".join(top_destinations_for_owner(conn, chat_id, 24, 3)) or "-",
                }
            )
        return out


def load_user_detail(
    chat_id: str,
    q: str = "",
    event_type: str = "",
    config_name: str = "",
    show_all_events: bool = False,
    events_page: int = 1,
    period: str = "24h",
    graph_scope: str = "all",
) -> dict[str, Any] | None:
    with db_conn() as conn:
        user = conn.execute(
            "SELECT chat_id, role, username, first_name, last_name, created_at, last_seen, max_clients FROM users WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        if not user:
            return None

        clients = conn.execute(
            "SELECT name, ip, pub, created_at FROM clients WHERE owner_chat_id=? ORDER BY created_at DESC",
            (chat_id,),
        ).fetchall()
        latest = get_latest_samples(conn)
        cfg_by_pub = {c["pub"]: c["name"] for c in clients}

        config_rows = []
        peer_pubs = [c["pub"] for c in clients]
        start_dt, bucket_seconds, bucket_count, period = period_params(conn, period, peer_pubs)
        labels = make_labels(start_dt, bucket_seconds, bucket_count)
        user_traffic = [0] * bucket_count
        user_hs = [0] * bucket_count
        for c in clients:
            sample = latest.get(c["pub"])
            rx24, tx24 = traffic_delta(conn, c["pub"], 24)
            rx7d, tx7d = traffic_delta(conn, c["pub"], 24 * 7)
            traffic_series, handshake_series = peer_series(conn, c["pub"], start_dt, bucket_seconds, bucket_count)
            for i in range(bucket_count):
                user_traffic[i] += traffic_series[i]
                user_hs[i] += handshake_series[i]
            traffic_sum = sum(traffic_series)
            hs_sum = sum(handshake_series)
            traffic_peak = max(traffic_series) if traffic_series else 0
            hs_peak = max(handshake_series) if handshake_series else 0
            speed_series_bps = [int((x * 8) / max(bucket_seconds, 1)) for x in traffic_series]
            config_rows.append(
                {
                    "name": c["name"],
                    "ip": c["ip"],
                    "pub": c["pub"],
                    "created_at": c["created_at"],
                    "endpoint": sample["endpoint"] if sample else "-",
                    "latest_handshake": epoch_to_iso(int(sample["latest_handshake"])) if sample and int(sample["latest_handshake"]) > 0 else "-",
                    "rx_total": human_bytes(int(sample["rx"])) if sample else "0 B",
                    "tx_total": human_bytes(int(sample["tx"])) if sample else "0 B",
                    "rx_24h": human_bytes(rx24),
                    "tx_24h": human_bytes(tx24),
                    "rx_7d": human_bytes(rx7d),
                    "tx_7d": human_bytes(tx7d),
                    "h24": handshake_count(conn, c["pub"], 24),
                    "h7d": handshake_count(conn, c["pub"], 24 * 7),
                    "traffic_series": traffic_series,
                    "speed_series_bps": speed_series_bps,
                    "handshake_series": handshake_series,
                    "traffic_total": human_bytes(traffic_sum),
                    "traffic_peak": human_bytes(traffic_peak),
                    "traffic_avg": human_bytes(int(traffic_sum / max(bucket_count, 1))) if traffic_sum > 0 else "0 B",
                    "traffic_total_gb": bytes_to_gb(traffic_sum),
                    "traffic_peak_gb": bytes_to_gb(traffic_peak),
                    "traffic_avg_gb": bytes_to_gb(int(traffic_sum / max(bucket_count, 1))) if traffic_sum > 0 else 0.0,
                    "traffic_total_gb_fmt": fmt2(bytes_to_gb(traffic_sum)),
                    "traffic_peak_gb_fmt": fmt2(bytes_to_gb(traffic_peak)),
                    "traffic_avg_gb_fmt": fmt2(bytes_to_gb(int(traffic_sum / max(bucket_count, 1))) if traffic_sum > 0 else 0.0),
                    "speed_peak_mbps": (max(speed_series_bps) / 1_000_000) if speed_series_bps else 0.0,
                    "speed_peak_mbps_fmt": fmt2((max(speed_series_bps) / 1_000_000) if speed_series_bps else 0.0),
                    "handshake_total": hs_sum,
                    "handshake_peak": hs_peak,
                }
            )

        events: list[dict[str, Any]] = []
        if peer_pubs:
            where = [f"peer_pub IN ({','.join('?' for _ in peer_pubs)})"]
            params: list[Any] = list(peer_pubs)
            if config_name:
                cfg_pubs = [c["pub"] for c in clients if c["name"] == config_name]
                if cfg_pubs:
                    where.append(f"peer_pub IN ({','.join('?' for _ in cfg_pubs)})")
                    params.extend(cfg_pubs)
                else:
                    where.append("1=0")
            if event_type:
                where.append("event_type=?")
                params.append(event_type)
            if q:
                where.append("(details LIKE ? OR peer_pub LIKE ?)")
                params.extend([f"%{q}%", f"%{q}%"])
            rows = conn.execute(
                f"""
                SELECT event_ts, peer_pub, event_type, details
                FROM wg_peer_events
                WHERE {' AND '.join(where)}
                ORDER BY id DESC
                LIMIT 300
                """,
                tuple(params),
            ).fetchall()
            for r in rows:
                cfg = cfg_by_pub.get(r["peer_pub"], "-")
                events.append(
                    {
                        "event_ts": r["event_ts"],
                        "peer_pub": r["peer_pub"],
                        "event_type": r["event_type"],
                        "details": r["details"] or "",
                        "config_name": cfg,
                    }
                )

        events_per_page = 100
        total_events = len(events)
        if not show_all_events:
            events_view = events[:20]
            events_total_pages = 1
            events_page = 1
        else:
            events_total_pages = max((total_events + events_per_page - 1) // events_per_page, 1)
            events_page = min(max(events_page, 1), events_total_pages)
            start = (events_page - 1) * events_per_page
            end = start + events_per_page
            events_view = events[start:end]

        filtered_config_rows = config_rows
        if config_name:
            filtered_config_rows = [c for c in config_rows if c["name"] == config_name]

        user_traffic_series = [0] * bucket_count
        user_hs_series = [0] * bucket_count
        for c in filtered_config_rows:
            for i in range(bucket_count):
                user_traffic_series[i] += c["traffic_series"][i]
                user_hs_series[i] += c["handshake_series"][i]
        user_speed_series_bps = [int((x * 8) / max(bucket_seconds, 1)) for x in user_traffic_series]

        return {
            "user": {
                "chat_id": user["chat_id"],
                "role": user["role"],
                "username": user["username"] or "",
                "full_name": " ".join(x for x in [user["first_name"], user["last_name"]] if x) or "",
                "created_at": user["created_at"],
                "last_seen": user["last_seen"] or "-",
                "max_clients": user["max_clients"],
            },
            "configs": filtered_config_rows,
            "events": events_view,
            "events_total": total_events,
            "events_show_all": show_all_events,
            "events_page": events_page,
            "events_total_pages": events_total_pages,
            "labels": labels,
            "user_traffic_series": user_traffic_series,
            "user_speed_series_bps": user_speed_series_bps,
            "user_handshake_series": user_hs_series,
            "user_traffic_total": human_bytes(sum(user_traffic_series)),
            "user_traffic_peak": human_bytes(max(user_traffic_series) if user_traffic_series else 0),
            "user_traffic_avg": human_bytes(int(sum(user_traffic_series) / max(bucket_count, 1))) if sum(user_traffic_series) > 0 else "0 B",
            "user_traffic_total_gb": bytes_to_gb(sum(user_traffic_series)),
            "user_traffic_peak_gb": bytes_to_gb(max(user_traffic_series) if user_traffic_series else 0),
            "user_traffic_avg_gb": bytes_to_gb(int(sum(user_traffic_series) / max(bucket_count, 1))) if sum(user_traffic_series) > 0 else 0.0,
            "user_traffic_total_gb_fmt": fmt2(bytes_to_gb(sum(user_traffic_series))),
            "user_traffic_peak_gb_fmt": fmt2(bytes_to_gb(max(user_traffic_series) if user_traffic_series else 0)),
            "user_traffic_avg_gb_fmt": fmt2(bytes_to_gb(int(sum(user_traffic_series) / max(bucket_count, 1))) if sum(user_traffic_series) > 0 else 0.0),
            "user_speed_peak_mbps": (max(user_speed_series_bps) / 1_000_000) if user_speed_series_bps else 0.0,
            "user_speed_peak_mbps_fmt": fmt2((max(user_speed_series_bps) / 1_000_000) if user_speed_series_bps else 0.0),
            "user_handshake_total": sum(user_hs_series),
            "user_handshake_peak": max(user_hs_series) if user_hs_series else 0,
            "event_types": ["", "handshake", "endpoint_change", "traffic"],
            "config_filter_values": [""] + sorted([c["name"] for c in clients]),
            "filters": {
                "q": q,
                "event_type": event_type,
                "config_name": config_name,
                "show_all_events": "1" if show_all_events else "0",
                "period": period,
                "graph_scope": graph_scope,
            },
            "period_values": [("1h", "1 час"), ("24h", "24 часа"), ("7d", "Неделя"), ("30d", "Месяц"), ("all", "Всё время")],
            "graph_scope_values": [("all", "Все графики"), ("user", "Только общий"), ("config", "Только по конфигам"), ("none", "Скрыть графики")],
            "bucket_seconds": bucket_seconds,
            "bucket_label": bucket_label_ru(bucket_seconds),
            "poll_seconds": MONITOR_POLL_SEC,
            "chat_file_exists": (CHAT_DIR / f"{chat_id}.log").exists(),
        }


async def collector_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(collect_once)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(5, MONITOR_POLL_SEC))
        except asyncio.TimeoutError:
            continue


@app.on_event("startup")
async def on_startup() -> None:
    init_monitor_db()
    app.state.collector_stop = asyncio.Event()
    app.state.collector_task = asyncio.create_task(collector_loop(app.state.collector_stop))


@app.on_event("shutdown")
async def on_shutdown() -> None:
    stop_event: asyncio.Event = app.state.collector_stop
    stop_event.set()
    await app.state.collector_task


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_auth(request):
        return RedirectResponse("/", status_code=302)
    return TEMPLATES.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if verify_credentials(username.strip(), password):
        request.session["auth_user"] = username.strip()
        return RedirectResponse("/", status_code=302)
    return TEMPLATES.TemplateResponse("login.html", {"request": request, "error": "Неверный логин или пароль."})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not is_auth(request):
        return redirect_login()
    return TEMPLATES.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "users": load_dashboard(),
            "auth_user": request.session.get("auth_user", ""),
            "updated_at": now_iso(),
        },
    )


@app.get("/user/{chat_id}", response_class=HTMLResponse)
async def user_detail(request: Request, chat_id: str):
    if not is_auth(request):
        return redirect_login()
    q = (request.query_params.get("q") or "").strip()
    event_type = (request.query_params.get("event_type") or "").strip()
    config_name = (request.query_params.get("config_name") or "").strip()
    show_all_events = (request.query_params.get("show_all_events") or "").strip() == "1"
    period = (request.query_params.get("period") or "24h").strip()
    graph_scope = (request.query_params.get("graph_scope") or "all").strip()
    try:
        events_page = int((request.query_params.get("events_page") or "1").strip())
    except Exception:
        events_page = 1
    data = load_user_detail(
        chat_id,
        q=q,
        event_type=event_type,
        config_name=config_name,
        show_all_events=show_all_events,
        events_page=events_page,
        period=period,
        graph_scope=graph_scope,
    )
    if not data:
        return RedirectResponse("/", status_code=302)
    return TEMPLATES.TemplateResponse("user.html", {"request": request, "data": data, "auth_user": request.session.get("auth_user", "")})


@app.get("/user/{chat_id}/chat")
async def download_chat(request: Request, chat_id: str):
    if not is_auth(request):
        return redirect_login()
    path = CHAT_DIR / f"{chat_id}.log"
    if not path.exists():
        return RedirectResponse(f"/user/{chat_id}", status_code=302)
    return FileResponse(path=str(path), filename=f"{chat_id}.log", media_type="text/plain")


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not is_auth(request):
        return redirect_login()
    return TEMPLATES.TemplateResponse("settings.html", {"request": request, "error": "", "ok": "", "auth_user": request.session.get("auth_user", "")})


@app.post("/settings", response_class=HTMLResponse)
async def settings_submit(
    request: Request,
    current_password: str = Form(...),
    new_username: str = Form(...),
    new_password: str = Form(...),
    repeat_password: str = Form(...),
):
    if not is_auth(request):
        return redirect_login()
    if new_password != repeat_password:
        return TEMPLATES.TemplateResponse(
            "settings.html",
            {"request": request, "error": "Новые пароли не совпадают.", "ok": "", "auth_user": request.session.get("auth_user", "")},
        )
    ok, msg = change_credentials(current_password, new_username, new_password)
    if ok:
        request.session["auth_user"] = new_username.strip()
    return TEMPLATES.TemplateResponse(
        "settings.html",
        {"request": request, "error": "" if ok else msg, "ok": msg if ok else "", "auth_user": request.session.get("auth_user", "")},
    )
