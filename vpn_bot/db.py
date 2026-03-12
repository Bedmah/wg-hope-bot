from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterable

from .settings import DB_PATH, SUPER_OWNER_CHAT_ID, DEFAULT_USER_LIMIT, ADMIN_LIMIT

ROLES = ("super_owner", "admin", "user", "pending", "banned")
BOT_TEXT_KEYS = ("user_guide", "support_text")


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
                created_at TEXT NOT NULL,
                UNIQUE(owner_chat_id, name),
                UNIQUE(ip)
            )
            """
        )

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


def add_client(owner_chat_id: str, stored_name: str, ip: str, pub: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO clients(owner_chat_id, name, ip, pub, created_at) VALUES(?,?,?,?,?)",
            (owner_chat_id, stored_name, ip, pub, now_iso()),
        )
        conn.commit()


def get_client(owner_chat_id: str, stored_name: str):
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE owner_chat_id=? AND name=?",
            (owner_chat_id, stored_name),
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
