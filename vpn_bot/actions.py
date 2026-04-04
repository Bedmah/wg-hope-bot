from __future__ import annotations

import json
import re
from pathlib import Path

from telegram.ext import ContextTypes

from . import db
from . import chatlog
from .routing import apply_client_egress_route, remove_client_egress_route
from .qr import make_qr
from .settings import CLIENTS_DIR, SUPPORT_HANDLE
from .wireguard import run, allocate_ip, validate_ip, add_peer, remove_peer, remove_peer_block, build_client_config

DEFAULT_USER_GUIDE = (
    "Инструкция по подключению:\n"
    "1) Нажми 'Добавить' и введи имя конфига.\n"
    "2) Получи файл .conf или QR от бота.\n"
    "3) Импортируй конфиг в приложение WireGuard (iOS/Android/Windows/macOS/Linux).\n"
    "4) Активируй туннель.\n"
    "5) Если не работает, проверь время на устройстве, интернет и что UDP-порт сервера доступен."
)

DEFAULT_SUPPORT_TEXT = f"Пиши сюда: {SUPPORT_HANDLE}"
DEFAULT_REGIONS_TEXT = (
    "Регионы:\n"
    "- Москва: прямой выход через сервер.\n"
    "- Латвия: выход через uplink.\n"
    "Выбирай регион в разделе 'Регион'."
)
DEFAULT_ABOUT_TEXT = (
    "О проекте:\n"
    "Сервис выдаёт персональные VPN-конфиги WireGuard и позволяет менять регион выхода в интернет."
)
DEFAULT_WIREGUARD_TEXT = (
    "WireGuard:\n"
    "Используйте официальный клиент WireGuard.\n"
    "Импортируйте .conf или QR из бота, включите туннель и проверьте подключение."
)


def get_user_guide() -> str:
    return db.get_bot_text("user_guide", DEFAULT_USER_GUIDE)


def get_support_text() -> str:
    return db.get_bot_text("support_text", DEFAULT_SUPPORT_TEXT)


def get_regions_text() -> str:
    return db.get_bot_text("regions_text", DEFAULT_REGIONS_TEXT)


def get_about_text() -> str:
    return db.get_bot_text("about_text", DEFAULT_ABOUT_TEXT)


def get_wireguard_text() -> str:
    return db.get_bot_text("wireguard_text", DEFAULT_WIREGUARD_TEXT)


def safe_name(name: str) -> str:
    name = re.sub(r"\s+", "_", name.strip(), flags=re.UNICODE)
    name = re.sub(r"[^\w.\-]", "_", name, flags=re.UNICODE)
    name = re.sub(r"_+", "_", name)
    return name.strip("._- ")


def user_dir(chat_id: str) -> Path:
    path = CLIENTS_DIR / chat_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def files_for(chat_id: str, display_name: str) -> tuple[str, dict[str, Path]]:
    stored = f"{chat_id}_{safe_name(display_name)}"
    base = user_dir(chat_id) / stored
    return stored, {
        "priv": base.with_suffix(".key"),
        "pub": base.with_suffix(".pub"),
        "conf": base.with_suffix(".conf"),
        "meta": base.with_suffix(".json"),
        "qr": base.with_suffix(".png"),
    }


def files_from_stored(chat_id: str, stored_name: str) -> dict[str, Path]:
    suffix = stored_name.split("_", 1)[1] if "_" in stored_name else stored_name
    _, files = files_for(chat_id, suffix)
    return files


def display_name_for(chat_id: str, stored_name: str) -> str:
    files = files_from_stored(chat_id, stored_name)
    meta = files.get("meta")
    if meta and meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8", errors="ignore"))
            value = (data.get("display_name") or "").strip()
            if value:
                return value
        except Exception:
            pass

    prefix = f"{chat_id}_"
    if stored_name.startswith(prefix):
        value = stored_name[len(prefix) :].strip()
        if value:
            return value
    return stored_name


async def say(context: ContextTypes.DEFAULT_TYPE, chat_id: str, text: str, kb=None) -> None:
    chatlog.append(chat_id, "bot", text)
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)


async def send_config_files(
    context: ContextTypes.DEFAULT_TYPE,
    dst_chat_id: str,
    conf_path: Path,
    qr_path: Path,
    display_name: str,
) -> None:
    if conf_path.exists():
        chatlog.append(dst_chat_id, "bot", f"[document] {display_name}.conf")
        with conf_path.open("rb") as f:
            await context.bot.send_document(chat_id=dst_chat_id, document=f, filename=f"{display_name}.conf")

    if qr_path.exists():
        chatlog.append(dst_chat_id, "bot", f"[photo] {display_name}.conf QR")
        with qr_path.open("rb") as f:
            await context.bot.send_photo(chat_id=dst_chat_id, photo=f, caption=f"{display_name}.conf QR")


async def create_client(
    context: ContextTypes.DEFAULT_TYPE,
    owner_chat_id: str,
    display_name: str,
    forced_ip: str | None = None,
) -> None:
    try:
        name = (display_name or "").strip()
        if not name:
            await say(context, owner_chat_id, "Пустое имя. Введи название конфига еще раз.")
            return
        if len(name) > 64:
            await say(context, owner_chat_id, "Слишком длинное имя. Максимум 64 символа.")
            return

        stored_name, files = files_for(owner_chat_id, name)
        if not safe_name(name):
            await say(
                context,
                owner_chat_id,
                "Недопустимое имя. Используй буквы, цифры, пробел, ., -, _ (русский язык поддерживается).",
            )
            return
        if files["meta"].exists():
            await say(context, owner_chat_id, "Такой конфиг уже существует.")
            return

        ip = forced_ip or allocate_ip()
        if forced_ip:
            validate_ip(forced_ip)

        private_key = run("wg genkey")
        public_key = run(f"echo '{private_key}' | wg pubkey")

        files["priv"].write_text(private_key, encoding="utf-8")
        files["pub"].write_text(public_key, encoding="utf-8")

        add_peer(public_key, ip)
        region = db.get_default_region_code()
        db.add_client(owner_chat_id, stored_name, ip, public_key, region=region)
        db.log_event("client_create", owner_chat_id, owner_chat_id, f"name={stored_name} ip={ip}")

        conf_text = build_client_config(private_key, ip)
        files["conf"].write_text(conf_text, encoding="utf-8")
        make_qr(conf_text, files["qr"])

        files["meta"].write_text(
            json.dumps(
                {
                    "owner_chat_id": owner_chat_id,
                    "name": stored_name,
                    "display_name": name,
                    "ip": ip,
                    "pub": public_key,
                    "region": region,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        try:
            apply_client_egress_route(ip, region)
        except Exception as route_exc:
            db.log_event("region_sync_warn", owner_chat_id, owner_chat_id, str(route_exc))

        await say(context, owner_chat_id, f"Готово. Конфиг: {name} | IP: {ip}")
        await send_config_files(context, owner_chat_id, files["conf"], files["qr"], name)
    except Exception as exc:
        db.log_event("client_create_error", owner_chat_id, owner_chat_id, str(exc))
        await say(context, owner_chat_id, f"Ошибка при создании: {exc}")


async def send_client(context: ContextTypes.DEFAULT_TYPE, owner_chat_id: str, stored_name: str) -> None:
    row = db.get_client(owner_chat_id, stored_name)
    if not row:
        await say(context, owner_chat_id, "Конфиг не найден.")
        return

    files = files_from_stored(owner_chat_id, stored_name)
    if files["conf"].exists() and not files["qr"].exists():
        make_qr(files["conf"].read_text(encoding="utf-8", errors="ignore"), files["qr"])

    db.log_event("client_send", owner_chat_id, owner_chat_id, f"name={stored_name}")
    await send_config_files(
        context,
        owner_chat_id,
        files["conf"],
        files["qr"],
        display_name_for(owner_chat_id, stored_name),
    )


async def send_client_to_admin(
    context: ContextTypes.DEFAULT_TYPE,
    owner_chat_id: str,
    stored_name: str,
    admin_chat_id: str,
) -> None:
    row = db.get_client(owner_chat_id, stored_name)
    if not row:
        await say(context, admin_chat_id, "Конфиг не найден.")
        return

    files = files_from_stored(owner_chat_id, stored_name)
    if files["conf"].exists() and not files["qr"].exists():
        make_qr(files["conf"].read_text(encoding="utf-8", errors="ignore"), files["qr"])

    db.log_event("client_send_admin", admin_chat_id, owner_chat_id, f"name={stored_name}")
    await send_config_files(
        context,
        admin_chat_id,
        files["conf"],
        files["qr"],
        display_name_for(owner_chat_id, stored_name),
    )


async def revoke_client(context: ContextTypes.DEFAULT_TYPE, owner_chat_id: str, stored_name: str) -> None:
    row = db.get_client(owner_chat_id, stored_name)
    if not row:
        await say(context, owner_chat_id, "Конфиг не найден.")
        return

    pub_key = row["pub"]
    try:
        remove_peer(pub_key)
        remove_peer_block(pub_key)
    except Exception as exc:
        db.log_event("client_revoke_warn", owner_chat_id, owner_chat_id, str(exc))

    db.delete_client(owner_chat_id, stored_name)

    files = files_from_stored(owner_chat_id, stored_name)
    for path in files.values():
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    try:
        remove_client_egress_route(row["ip"])
    except Exception as route_exc:
        db.log_event("region_sync_warn", owner_chat_id, owner_chat_id, str(route_exc))

    db.log_event("client_revoke", owner_chat_id, owner_chat_id, f"name={stored_name}")
    await say(context, owner_chat_id, f"Конфиг {display_name_for(owner_chat_id, stored_name)} удален.")


def purge_user_clients(owner_chat_id: str) -> int:
    rows = db.list_clients(owner_chat_id)
    removed = 0
    for row in rows:
        stored_name = row["name"]
        pub_key = row["pub"]

        try:
            remove_peer(pub_key)
            remove_peer_block(pub_key)
        except Exception:
            pass

        db.delete_client(owner_chat_id, stored_name)
        try:
            remove_client_egress_route(row["ip"])
        except Exception:
            pass
        files = files_from_stored(owner_chat_id, stored_name)
        for path in files.values():
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
        removed += 1

    db.log_event("user_purge", owner_chat_id, owner_chat_id, f"removed={removed}")
    return removed


def format_users(rows, title: str = "Пользователи") -> str:
    if not rows:
        return "Пользователей нет."

    lines = [f"{title}: {len(rows)}"]
    for idx, row in enumerate(rows, start=1):
        login = f"@{row['username']}" if row["username"] else "(без username)"
        full_name = " ".join(x for x in [row["first_name"], row["last_name"]] if x) or "(без имени)"
        last_seen = row["last_seen"] or "-"
        lines.extend(
            [
                f"{idx}. {login} ({full_name})",
                f"chat_id: {row['chat_id']}",
                f"роль: {row['role']} | лимит: {row['max_clients']}",
                f"создан: {row['created_at']}",
                f"последняя активность: {last_seen}",
                "",
            ]
        )
    return "\n".join(lines)


def format_logs(rows) -> str:
    if not rows:
        return "Логи пусты."

    lines = []
    for row in rows:
        details = row["details"] or ""
        if len(details) > 300:
            details = details[:300] + "..."
        lines.append(
            f"[{row['ts']}] action={row['action']} actor={row['actor_chat_id']} "
            f"target={row['target_chat_id']} details={details}"
        )
    return "\n".join(lines)
