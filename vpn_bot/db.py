from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterable

from .regions import (
    REGION_AMSTERDAM,
    REGION_DEFAULT,
    REGION_LATVIA,
    REGION_MOSCOW,
    normalize_region,
)
from .settings import DB_PATH, SUPER_OWNER_CHAT_ID, DEFAULT_USER_LIMIT, ADMIN_LIMIT

ROLES = ("super_owner", "admin", "user", "pending", "banned")
BOT_TEXT_KEYS = ("user_guide", "support_text")
UPLINK_TYPES = ("system", "amneziawg", "wireguard")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def init() -> None:
    with _db() as conn:
        cur = conn.cursor()

        existing = cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        if existing and existing["sql"] and "super_owner" not in existing["sql"]:
            cur.execute("ALTER TABLE users RENAME TO users_old")
            cur.execute(
                """
                CREATE TABLE users(
                    chat_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('super_owner','admin','user','pending','banned')),
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    max_clients INTEGER NOT NULL,
                    last_seen TEXT
                )
                """
            )
            old_cols = _table_columns(cur, "users_old")
            common_cols = [
                col
                for col in (
                    "chat_id",
                    "role",
                    "username",
                    "first_name",
                    "last_name",
                    "created_at",
                    "updated_at",
                    "max_clients",
                    "last_seen",
                )
                if col in old_cols
            ]
            cols = ", ".join(common_cols)
            cur.execute(f"INSERT INTO users({cols}) SELECT {cols} FROM users_old")
            cur.execute("DROP TABLE users_old")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                chat_id TEXT PRIMARY KEY,
                role TEXT NOT NULL CHECK(role IN ('super_owner','admin','user','pending','banned')),
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                max_clients INTEGER NOT NULL,
                last_seen TEXT
            )
            """
        )

        # Lightweight migrations for older schemas.
        cols = _table_columns(cur, "users")
        if "max_clients" not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN max_clients INTEGER")
            cur.execute("UPDATE users SET max_clients=? WHERE max_clients IS NULL", (DEFAULT_USER_LIMIT,))
        if "last_seen" not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN last_seen TEXT")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS clients(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_chat_id TEXT NOT NULL,
                name TEXT NOT NULL,
                ip TEXT NOT NULL,
                pub TEXT NOT NULL,
                region TEXT NOT NULL DEFAULT 'latvia',
                created_at TEXT NOT NULL,
                UNIQUE(owner_chat_id, name),
                UNIQUE(ip)
            )
            """
        )
        client_cols = _table_columns(cur, "clients")
        if "region" not in client_cols:
            cur.execute("ALTER TABLE clients ADD COLUMN region TEXT")
            cur.execute("UPDATE clients SET region=? WHERE region IS NULL OR TRIM(region)=''", (REGION_DEFAULT,))
        cur.execute("UPDATE clients SET region=? WHERE region IS NULL OR TRIM(region)=''", (REGION_DEFAULT,))
        for row in cur.execute("SELECT id, region FROM clients").fetchall():
            normalized = normalize_region(row["region"])
            if normalized != row["region"]:
                cur.execute("UPDATE clients SET region=? WHERE id=?", (normalized, row["id"]))

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS logs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                actor_chat_id TEXT,
                action TEXT NOT NULL,
                target_chat_id TEXT,
                details TEXT
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS uplink_interfaces(
                name TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                config_path TEXT,
                service_name TEXT,
                table_id INTEGER,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS uplink_regions(
                code TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                interface_name TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS uplink_health(
                interface_name TEXT PRIMARY KEY,
                is_ok INTEGER NOT NULL,
                details TEXT,
                updated_at TEXT NOT NULL,
                last_alert_state TEXT,
                last_alert_at TEXT
            )
            """
        )

        iface_cols = _table_columns(cur, "uplink_interfaces")
        if "table_id" not in iface_cols:
            cur.execute("ALTER TABLE uplink_interfaces ADD COLUMN table_id INTEGER")
        if "enabled" not in iface_cols:
            cur.execute("ALTER TABLE uplink_interfaces ADD COLUMN enabled INTEGER")
            cur.execute("UPDATE uplink_interfaces SET enabled=1 WHERE enabled IS NULL")

        ts = now_iso()
        iface_count_row = cur.execute("SELECT COUNT(*) AS n FROM uplink_interfaces").fetchone()
        iface_count = int(iface_count_row["n"]) if iface_count_row else 0
        region_count_row = cur.execute("SELECT COUNT(*) AS n FROM uplink_regions").fetchone()
        region_count = int(region_count_row["n"]) if region_count_row else 0

        # Seed defaults only for a fresh DB, do not resurrect manually deleted entities on every restart.
        if iface_count == 0:
            cur.execute(
                """
                INSERT INTO uplink_interfaces(name, kind, config_path, service_name, table_id, enabled, created_at, updated_at)
                VALUES('eth0','system',NULL,NULL,NULL,1,?,?)
                """,
                (ts, ts),
            )
            cur.execute(
                """
                INSERT INTO uplink_interfaces(name, kind, config_path, service_name, table_id, enabled, created_at, updated_at)
                VALUES('aw-lv','amneziawg','/etc/amnezia/amneziawg/aw-lv.conf','amnezia-awg@aw-lv.service',166,1,?,?)
                """,
                (ts, ts),
            )
            cur.execute(
                """
                INSERT INTO uplink_interfaces(name, kind, config_path, service_name, table_id, enabled, created_at, updated_at)
                VALUES('aw-am','amneziawg','/etc/amnezia/amneziawg/aw-am.conf','amnezia-awg@aw-am.service',167,1,?,?)
                """,
                (ts, ts),
            )

        if region_count == 0:
            cur.execute(
                """
                INSERT INTO uplink_regions(code, label, interface_name, is_default, created_at, updated_at)
                VALUES(?,?,?,?,?,?)
                """,
                (REGION_MOSCOW, "Москва", "eth0", 0, ts, ts),
            )
            cur.execute(
                """
                INSERT INTO uplink_regions(code, label, interface_name, is_default, created_at, updated_at)
                VALUES(?,?,?,?,?,?)
                """,
                (REGION_LATVIA, "Латвия", "aw-lv", 1, ts, ts),
            )
            cur.execute(
                """
                INSERT INTO uplink_regions(code, label, interface_name, is_default, created_at, updated_at)
                VALUES(?,?,?,?,?,?)
                """,
                (REGION_AMSTERDAM, "Амстердам", "aw-am", 0, ts, ts),
            )

        default_row = cur.execute(
            "SELECT code FROM uplink_regions WHERE is_default=1 ORDER BY code LIMIT 1"
        ).fetchone()
        if default_row:
            default_code = default_row["code"]
        else:
            first_region = cur.execute("SELECT code FROM uplink_regions ORDER BY code LIMIT 1").fetchone()
            default_code = first_region["code"] if first_region else REGION_DEFAULT
            if first_region:
                cur.execute("UPDATE uplink_regions SET is_default=0")
                cur.execute("UPDATE uplink_regions SET is_default=1, updated_at=? WHERE code=?", (now_iso(), default_code))
        valid_codes = {r["code"] for r in cur.execute("SELECT code FROM uplink_regions").fetchall()}
        for row in cur.execute("SELECT id, region FROM clients").fetchall():
            norm = normalize_region(row["region"])
            if norm not in valid_codes:
                norm = default_code
            if norm != row["region"]:
                cur.execute("UPDATE clients SET region=? WHERE id=?", (norm, row["id"]))

        if SUPER_OWNER_CHAT_ID:
            row = cur.execute("SELECT chat_id FROM users WHERE chat_id=?", (SUPER_OWNER_CHAT_ID,)).fetchone()
            if row:
                cur.execute(
                    "UPDATE users SET role='super_owner', max_clients=?, updated_at=? WHERE chat_id=?",
                    (ADMIN_LIMIT, now_iso(), SUPER_OWNER_CHAT_ID),
                )
            else:
                ts = now_iso()
                cur.execute(
                    """
                    INSERT INTO users(chat_id, role, created_at, updated_at, max_clients, last_seen)
                    VALUES(?, 'super_owner', ?, ?, ?, ?)
                    """,
                    (SUPER_OWNER_CHAT_ID, ts, ts, ADMIN_LIMIT, ts),
                )

        defaults = {
            "user_guide": (
                "Инструкция по подключению:\n"
                "1) Нажми 'Добавить' и введи имя конфига.\n"
                "2) Получи файл .conf или QR от бота.\n"
                "3) Импортируй конфиг в приложение WireGuard (iOS/Android/Windows/macOS/Linux).\n"
                "4) Активируй туннель.\n"
                "5) Если не работает, проверь время на устройстве, интернет и что UDP-порт сервера доступен."
            ),
            "support_text": "Пиши сюда: @support",
        }
        for key, value in defaults.items():
            cur.execute(
                "INSERT OR IGNORE INTO bot_settings(key, value, updated_at) VALUES(?,?,?)",
                (key, value, now_iso()),
            )

        conn.commit()


def upsert_user(chat_id: str, username: str | None, first_name: str | None, last_name: str | None) -> None:
    with _db() as conn:
        cur = conn.cursor()
        row = cur.execute("SELECT role, max_clients FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        ts = now_iso()
        if row:
            cur.execute(
                """
                UPDATE users
                SET username=?, first_name=?, last_name=?, updated_at=?, last_seen=?
                WHERE chat_id=?
                """,
                (username, first_name, last_name, ts, ts, chat_id),
            )
        else:
            role = "super_owner" if SUPER_OWNER_CHAT_ID and chat_id == SUPER_OWNER_CHAT_ID else "pending"
            limit = ADMIN_LIMIT if role in ("super_owner", "admin") else DEFAULT_USER_LIMIT
            cur.execute(
                """
                INSERT INTO users(chat_id, role, username, first_name, last_name, created_at, updated_at, max_clients, last_seen)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (chat_id, role, username, first_name, last_name, ts, ts, limit, ts),
            )
        conn.commit()


def touch_seen(chat_id: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE users SET last_seen=?, updated_at=? WHERE chat_id=?", (now_iso(), now_iso(), chat_id))
        conn.commit()


def role(chat_id: str) -> str | None:
    with _db() as conn:
        row = conn.execute("SELECT role FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        return row["role"] if row else None


def set_role(chat_id: str, new_role: str) -> None:
    if new_role not in ROLES:
        raise ValueError("unknown role")

    with _db() as conn:
        cur = conn.cursor()
        row = cur.execute("SELECT chat_id, max_clients FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        ts = now_iso()
        limit = ADMIN_LIMIT if new_role in ("super_owner", "admin") else DEFAULT_USER_LIMIT
        if row:
            cur.execute(
                "UPDATE users SET role=?, max_clients=?, updated_at=? WHERE chat_id=?",
                (new_role, limit, ts, chat_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO users(chat_id, role, created_at, updated_at, max_clients, last_seen)
                VALUES(?,?,?,?,?,?)
                """,
                (chat_id, new_role, ts, ts, limit, ts),
            )
        conn.commit()


def get_limit(chat_id: str) -> int:
    with _db() as conn:
        row = conn.execute("SELECT max_clients FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        return int(row["max_clients"]) if row and row["max_clients"] is not None else DEFAULT_USER_LIMIT


def set_limit(chat_id: str, limit: int) -> bool:
    with _db() as conn:
        cur = conn.execute("UPDATE users SET max_clients=?, updated_at=? WHERE chat_id=?", (int(limit), now_iso(), chat_id))
        conn.commit()
        return cur.rowcount > 0


def get_bot_text(key: str, fallback: str = "") -> str:
    if key not in BOT_TEXT_KEYS:
        return fallback
    with _db() as conn:
        row = conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
        if not row:
            return fallback
        return row["value"] or fallback


def set_bot_text(key: str, value: str) -> None:
    if key not in BOT_TEXT_KEYS:
        raise ValueError("unknown bot text key")
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO bot_settings(key, value, updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, now_iso()),
        )
        conn.commit()


def users_by_role(role_name: str):
    with _db() as conn:
        return conn.execute(
            """
            SELECT chat_id, role, username, first_name, last_name, created_at, last_seen, max_clients
            FROM users WHERE role=? ORDER BY created_at
            """,
            (role_name,),
        ).fetchall()


def all_users():
    with _db() as conn:
        return conn.execute(
            """
            SELECT chat_id, role, username, first_name, last_name, created_at, last_seen, max_clients
            FROM users ORDER BY created_at
            """
        ).fetchall()


def approved_chat_ids() -> list[str]:
    with _db() as conn:
        rows = conn.execute("SELECT chat_id FROM users WHERE role IN ('super_owner','admin','user')").fetchall()
        return [r["chat_id"] for r in rows]


def add_client(owner_chat_id: str, stored_name: str, ip: str, pub: str, region: str | None = None) -> None:
    region_code = normalize_region(region)
    if not region_exists(region_code):
        region_code = get_default_region_code()
    with _db() as conn:
        conn.execute(
            "INSERT INTO clients(owner_chat_id, name, ip, pub, region, created_at) VALUES(?,?,?,?,?,?)",
            (owner_chat_id, stored_name, ip, pub, region_code, now_iso()),
        )
        conn.commit()


def get_client(owner_chat_id: str, stored_name: str):
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE owner_chat_id=? AND name=?",
            (owner_chat_id, stored_name),
        ).fetchone()


def get_client_by_id(owner_chat_id: str, client_id: int):
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE owner_chat_id=? AND id=?",
            (owner_chat_id, int(client_id)),
        ).fetchone()


def list_clients(owner_chat_id: str):
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE owner_chat_id=? ORDER BY created_at DESC",
            (owner_chat_id,),
        ).fetchall()


def list_all_clients():
    with _db() as conn:
        return conn.execute("SELECT * FROM clients ORDER BY created_at DESC").fetchall()


def client_count(owner_chat_id: str) -> int:
    with _db() as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM clients WHERE owner_chat_id=?", (owner_chat_id,)).fetchone()
        return int(row["cnt"]) if row else 0


def delete_client(owner_chat_id: str, stored_name: str) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM clients WHERE owner_chat_id=? AND name=?", (owner_chat_id, stored_name))
        conn.commit()


def set_client_region(owner_chat_id: str, client_id: int, region: str) -> bool:
    region_code = normalize_region(region)
    if not region_exists(region_code):
        return False
    with _db() as conn:
        cur = conn.execute(
            "UPDATE clients SET region=? WHERE owner_chat_id=? AND id=?",
            (region_code, owner_chat_id, int(client_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def list_uplink_interfaces():
    with _db() as conn:
        return conn.execute("SELECT * FROM uplink_interfaces ORDER BY name").fetchall()


def get_uplink_interface(name: str):
    with _db() as conn:
        return conn.execute("SELECT * FROM uplink_interfaces WHERE name=?", (name.strip(),)).fetchone()


def next_table_id(start: int = 200) -> int:
    with _db() as conn:
        rows = conn.execute("SELECT table_id FROM uplink_interfaces WHERE table_id IS NOT NULL").fetchall()
        used = {int(r["table_id"]) for r in rows if r["table_id"] is not None}
    table_id = int(start)
    while table_id in used:
        table_id += 1
    return table_id


def upsert_uplink_interface(
    name: str,
    kind: str,
    config_path: str | None,
    service_name: str | None,
    table_id: int | None,
    enabled: int = 1,
) -> None:
    iface = name.strip()
    if not iface:
        raise ValueError("empty interface name")
    if kind not in UPLINK_TYPES:
        raise ValueError("bad interface kind")
    ts = now_iso()
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO uplink_interfaces(name, kind, config_path, service_name, table_id, enabled, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                kind=excluded.kind,
                config_path=excluded.config_path,
                service_name=excluded.service_name,
                table_id=excluded.table_id,
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
            """,
            (iface, kind, config_path, service_name, table_id, int(enabled), ts, ts),
        )
        conn.commit()


def delete_uplink_interface(name: str) -> bool:
    iface = name.strip()
    with _db() as conn:
        in_use = conn.execute("SELECT COUNT(*) AS n FROM uplink_regions WHERE interface_name=?", (iface,)).fetchone()
        if in_use and int(in_use["n"]) > 0:
            return False
        cur = conn.execute("DELETE FROM uplink_interfaces WHERE name=?", (iface,))
        conn.commit()
        return cur.rowcount > 0


def list_regions():
    with _db() as conn:
        return conn.execute("SELECT * FROM uplink_regions ORDER BY label, code").fetchall()


def get_region(code: str):
    with _db() as conn:
        return conn.execute("SELECT * FROM uplink_regions WHERE code=?", (normalize_region(code),)).fetchone()


def region_exists(code: str) -> bool:
    with _db() as conn:
        row = conn.execute("SELECT code FROM uplink_regions WHERE code=?", (normalize_region(code),)).fetchone()
        return bool(row)


def get_default_region_code() -> str:
    with _db() as conn:
        row = conn.execute("SELECT code FROM uplink_regions WHERE is_default=1 ORDER BY code LIMIT 1").fetchone()
        if row:
            return row["code"]
        row_any = conn.execute("SELECT code FROM uplink_regions ORDER BY code LIMIT 1").fetchone()
        return row_any["code"] if row_any else REGION_DEFAULT


def region_label_by_code(code: str) -> str:
    with _db() as conn:
        row = conn.execute("SELECT label FROM uplink_regions WHERE code=?", (normalize_region(code),)).fetchone()
        return row["label"] if row else normalize_region(code)


def upsert_region(code: str, label: str, interface_name: str, is_default: int = 0) -> None:
    region_code = normalize_region(code)
    if not region_code:
        raise ValueError("empty region code")
    if not get_uplink_interface(interface_name):
        raise ValueError("interface not found")
    ts = now_iso()
    with _db() as conn:
        if int(is_default):
            conn.execute("UPDATE uplink_regions SET is_default=0")
        conn.execute(
            """
            INSERT INTO uplink_regions(code, label, interface_name, is_default, created_at, updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(code) DO UPDATE SET
                label=excluded.label,
                interface_name=excluded.interface_name,
                is_default=excluded.is_default,
                updated_at=excluded.updated_at
            """,
            (region_code, label.strip() or region_code, interface_name.strip(), int(is_default), ts, ts),
        )
        conn.commit()


def delete_region(code: str, move_clients_to: str | None = None) -> bool:
    region_code = normalize_region(code)
    fallback = normalize_region(move_clients_to) if move_clients_to else get_default_region_code()
    with _db() as conn:
        row = conn.execute("SELECT * FROM uplink_regions WHERE code=?", (region_code,)).fetchone()
        if not row:
            return False
        if int(row["is_default"]) == 1 and fallback == region_code:
            return False
        if fallback == region_code or not conn.execute("SELECT code FROM uplink_regions WHERE code=?", (fallback,)).fetchone():
            other = conn.execute("SELECT code FROM uplink_regions WHERE code<>? ORDER BY code LIMIT 1", (region_code,)).fetchone()
            if not other:
                return False
            fallback = other["code"]
        conn.execute("UPDATE clients SET region=? WHERE region=?", (fallback, region_code))
        conn.execute("DELETE FROM uplink_regions WHERE code=?", (region_code,))
        if row["is_default"]:
            conn.execute("UPDATE uplink_regions SET is_default=1 WHERE code=?", (fallback,))
        conn.commit()
        return True


def set_default_region(code: str) -> bool:
    region_code = normalize_region(code)
    with _db() as conn:
        row = conn.execute("SELECT code FROM uplink_regions WHERE code=?", (region_code,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE uplink_regions SET is_default=0")
        conn.execute("UPDATE uplink_regions SET is_default=1, updated_at=? WHERE code=?", (now_iso(), region_code))
        conn.commit()
        return True


def admin_chat_ids() -> list[str]:
    with _db() as conn:
        rows = conn.execute("SELECT chat_id FROM users WHERE role IN ('super_owner','admin')").fetchall()
    result = [str(r["chat_id"]) for r in rows if str(r["chat_id"]).isdigit()]
    if SUPER_OWNER_CHAT_ID and str(SUPER_OWNER_CHAT_ID).isdigit() and SUPER_OWNER_CHAT_ID not in result:
        result.append(SUPER_OWNER_CHAT_ID)
    return result


def set_uplink_health(interface_name: str, is_ok: bool, details: str, last_alert_state: str | None = None) -> None:
    with _db() as conn:
        existing = conn.execute(
            "SELECT last_alert_state, last_alert_at FROM uplink_health WHERE interface_name=?",
            (interface_name,),
        ).fetchone()
        alert_state = last_alert_state if last_alert_state is not None else (existing["last_alert_state"] if existing else None)
        alert_at = now_iso() if last_alert_state is not None else (existing["last_alert_at"] if existing else None)
        conn.execute(
            """
            INSERT INTO uplink_health(interface_name, is_ok, details, updated_at, last_alert_state, last_alert_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(interface_name) DO UPDATE SET
                is_ok=excluded.is_ok,
                details=excluded.details,
                updated_at=excluded.updated_at,
                last_alert_state=excluded.last_alert_state,
                last_alert_at=excluded.last_alert_at
            """,
            (interface_name, 1 if is_ok else 0, details, now_iso(), alert_state, alert_at),
        )
        conn.commit()


def get_uplink_health(interface_name: str):
    with _db() as conn:
        return conn.execute("SELECT * FROM uplink_health WHERE interface_name=?", (interface_name,)).fetchone()


def used_ips_from_db() -> set[str]:
    with _db() as conn:
        rows = conn.execute("SELECT ip FROM clients").fetchall()
        return {r["ip"] for r in rows}


def log_event(action: str, actor_chat_id: str | None, target_chat_id: str | None, details: str | None) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO logs(ts, actor_chat_id, action, target_chat_id, details) VALUES(?,?,?,?,?)",
            (now_iso(), actor_chat_id, action, target_chat_id, details),
        )
        conn.commit()


def logs_recent(limit: int = 50):
    with _db() as conn:
        return conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()


def logs_for_user(chat_id: str, limit: int = 100):
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM logs WHERE actor_chat_id=? OR target_chat_id=? ORDER BY id DESC LIMIT ?",
            (chat_id, chat_id, int(limit)),
        ).fetchall()


def stats() -> dict:
    with _db() as conn:
        result: dict[str, int | list[str]] = {}
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        result["total_users"] = int(row["n"]) if row else 0

        for role_name in ("super_owner", "admin", "user", "pending", "banned"):
            r = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role=?", (role_name,)).fetchone()
            result[f"role_{role_name}"] = int(r["n"]) if r else 0

        row = conn.execute("SELECT COUNT(*) AS n FROM clients").fetchone()
        result["total_clients"] = int(row["n"]) if row else 0
        rows = conn.execute("SELECT ip FROM clients ORDER BY ip").fetchall()
        result["used_ips"] = [r["ip"] for r in rows]
        return result
