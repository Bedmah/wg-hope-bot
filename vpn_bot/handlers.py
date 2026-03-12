from __future__ import annotations

import ipaddress

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from . import db
from . import chatlog
from .regions import normalize_region
from .routing import sync_client_egress_routes
from .server_admin import (
    add_interface,
    add_or_update_region,
    delete_interface,
    interface_status,
    list_interfaces_text,
    list_regions_text,
    remove_region,
    replace_interface_config,
    set_default_region,
)
from .actions import (
    say,
    create_client,
    send_client,
    revoke_client,
    send_client_to_admin,
    purge_user_clients,
    display_name_for,
    format_users,
    format_logs,
    get_user_guide,
    get_support_text,
)
from .keyboards import (
    bottom_menu,
    admin_main_menu,
    admin_users_menu,
    admin_logs_menu,
    admin_customize_menu,
    admin_servers_menu,
    cancel_menu,
    pending_menu,
    clients_kb,
    admin_user_clients_kb,
    region_clients_kb,
    region_pick_kb,
    servers_delete_iface_kb,
    servers_delete_region_kb,
    BUTTON_ADD,
    BUTTON_LIST,
    BUTTON_GUIDE,
    BUTTON_REGION,
    BUTTON_SUPPORT,
    BUTTON_ADMIN,
    BUTTON_BACK,
    BUTTON_A_USERS,
    BUTTON_A_CLIENTS,
    BUTTON_A_SYNC_PROFILES,
    BUTTON_A_MONITORING,
    BUTTON_A_CUSTOMIZE,
    BUTTON_A_BROADCAST,
    BUTTON_A_LIMITS,
    BUTTON_A_STATS,
    BUTTON_A_LOGS,
    BUTTON_A_SERVERS,
    BUTTON_U_PENDING,
    BUTTON_U_ACTIVE,
    BUTTON_U_BANNED,
    BUTTON_U_ADD,
    BUTTON_U_BAN,
    BUTTON_U_ADMINS,
    BUTTON_U_PROMOTE,
    BUTTON_U_DEMOTE,
    BUTTON_L_RECENT,
    BUTTON_L_BY_USER,
    BUTTON_L_CHAT_FILE,
    BUTTON_P_STATUS,
    BUTTON_P_SUPPORT,
    BUTTON_C_VIEW,
    BUTTON_C_GUIDE,
    BUTTON_C_SUPPORT,
    BUTTON_S_LIST_IFACES,
    BUTTON_S_ADD_IFACE,
    BUTTON_S_DEL_IFACE,
    BUTTON_S_CFG_IFACE,
    BUTTON_S_LIST_REGIONS,
    BUTTON_S_ADD_REGION,
    BUTTON_S_DEL_REGION,
    BUTTON_S_DEFAULT_REGION,
    BUTTON_S_STATUS,
)
from .settings import SUPER_OWNER_CHAT_ID, VPN_SUBNET, CHAT_DIR, MONITOR_URL


def is_adminish(role: str | None) -> bool:
    return role in ("super_owner", "admin")


def is_super_owner(role: str | None) -> bool:
    return role == "super_owner"


def sync_user_profile(update: Update) -> str:
    user = update.effective_user
    chat_id = str(update.effective_chat.id)
    db.upsert_user(chat_id, user.username if user else None, user.first_name if user else None, user.last_name if user else None)
    return chat_id


def main_menu_for(role: str | None):
    return bottom_menu(is_adminish(role))


def admin_menu_for(role: str | None):
    return admin_main_menu(is_super_owner(role))


def menu_for_ui(role: str | None, ui_menu: str):
    if role == "pending":
        return pending_menu()
    if ui_menu == "admin_main":
        return admin_menu_for(role)
    if ui_menu == "admin_users":
        return admin_users_menu(is_super_owner(role))
    if ui_menu == "admin_logs":
        return admin_logs_menu()
    if ui_menu == "admin_customize":
        return admin_customize_menu()
    if ui_menu == "admin_servers":
        return admin_servers_menu()
    return main_menu_for(role)


def clients_region_text(rows) -> str:
    if not rows:
        return "У вас пока нет конфигов."
    lines = ["Текущие регионы ваших конфигов:"]
    for row in rows:
        label = display_name_for(str(row["owner_chat_id"]), row["name"])
        lines.append(f"- {label} | {row['ip']} | {db.region_label_by_code(row['region'])}")
    return "\n".join(lines)


def interfaces_for_delete_text() -> tuple[str, list[str]]:
    rows = db.list_uplink_interfaces()
    if not rows:
        return "Интерфейсов нет.", []
    lines = ["Выбери интерфейс для удаления:"]
    names: list[str] = []
    for row in rows:
        name = str(row["name"])
        names.append(name)
        lines.append(f"- {name} | kind={row['kind']} | enabled={row['enabled']}")
    return "\n".join(lines), names


def regions_for_delete_text() -> tuple[str, list[tuple[str, str]]]:
    rows = db.list_regions()
    if not rows:
        return "Регионов нет.", []
    lines = ["Выбери регион для удаления:"]
    items: list[tuple[str, str]] = []
    for row in rows:
        code = str(row["code"])
        label = str(row["label"])
        default_tag = " (default)" if int(row["is_default"]) == 1 else ""
        items.append((code, label))
        lines.append(f"- {label} [{code}] -> {row['interface_name']}{default_tag}")
    return "\n".join(lines), items


async def send_chunks(context: ContextTypes.DEFAULT_TYPE, chat_id: str, text: str, kb=None) -> None:
    chunk_size = 3900
    if len(text) <= chunk_size:
        chatlog.append(chat_id, "bot", text)
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
        return

    pos = 0
    while pos < len(text):
        part = text[pos : pos + chunk_size]
        pos += chunk_size
        chatlog.append(chat_id, "bot", part)
        await context.bot.send_message(
            chat_id=chat_id,
            text=part,
            reply_markup=kb if pos >= len(text) else None,
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db.init()
    user = update.effective_user
    chat_id = sync_user_profile(update)
    chatlog.append(chat_id, "user", "/start")
    db.log_event("start", chat_id, chat_id, None)

    role = db.role(chat_id)
    if role in (None, "pending"):
        await say(
            context,
            chat_id,
            "Доступ запрошен. Ожидайте одобрения администратора.\n"
            "Вы можете использовать кнопки: 'Проверить статус' и 'Доступ / Вопросы'.",
            pending_menu(),
        )
        if SUPER_OWNER_CHAT_ID and SUPER_OWNER_CHAT_ID != chat_id:
            try:
                username = f"@{user.username}" if user and user.username else "(без username)"
                full_name = " ".join(x for x in [user.first_name if user else None, user.last_name if user else None] if x)
                await context.bot.send_message(
                    chat_id=int(SUPER_OWNER_CHAT_ID),
                    text=f"Новая заявка: {username} {full_name} chat_id={chat_id}",
                )
            except Exception:
                pass
        return

    if role == "banned":
        await say(context, chat_id, "Доступ заблокирован.")
        return

    context.user_data.clear()
    context.user_data["ui_menu"] = "main"
    await say(context, chat_id, "Меню", main_menu_for(role))


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = sync_user_profile(update)
    chatlog.append(chat_id, "user", update.message.text if update.message and update.message.text else "/add")
    role = db.role(chat_id)
    if role not in ("super_owner", "admin", "user"):
        await say(context, chat_id, "Нет доступа.")
        return

    if not context.args:
        await say(context, chat_id, "Использование: /add <name> [ip]")
        return

    if db.client_count(chat_id) >= db.get_limit(chat_id):
        await say(context, chat_id, f"Лимит {db.get_limit(chat_id)} конфигов исчерпан.")
        return

    name = context.args[0]
    ip = context.args[1] if len(context.args) > 1 else None
    await create_client(context, chat_id, name, ip)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = sync_user_profile(update)
    db.touch_seen(chat_id)

    role = db.role(chat_id)
    if role == "pending":
        lower = ((update.message.text or "").strip()).lower()
        text = (update.message.text or "").strip()
        if text == BUTTON_P_STATUS:
            await say(context, chat_id, "Статус: заявка отправлена, ожидайте одобрения администратора.", pending_menu())
            return
        if text == BUTTON_P_SUPPORT or "вопрос" in lower or "поддерж" in lower:
            await say(context, chat_id, get_support_text(), pending_menu())
            return
        await say(context, chat_id, "Выберите действие:", pending_menu())
        return

    if role not in ("super_owner", "admin", "user"):
        db.log_event("denied_message", chat_id, chat_id, None)
        await say(context, chat_id, "Нет доступа.")
        return

    text = (update.message.text or "").strip()
    chatlog.append(chat_id, "user", text)
    lower = text.lower()
    ui_menu = context.user_data.get("ui_menu", "main")

    if text == BUTTON_BACK:
        if context.user_data.get("admin_mode"):
            context.user_data.pop("admin_mode", None)
            context.user_data.pop("broadcast_targets", None)
            context.user_data.pop("ban_target", None)
            context.user_data.pop("limit_target", None)
            await say(context, chat_id, "Действие отменено.", menu_for_ui(role, ui_menu))
            return

        if context.user_data.get("user_mode"):
            context.user_data["user_mode"] = None
            await say(context, chat_id, "Действие отменено.", main_menu_for(role))
            return

        if ui_menu in ("admin_users", "admin_logs", "admin_customize", "admin_servers"):
            context.user_data["ui_menu"] = "admin_main"
            await say(context, chat_id, "Админка", admin_menu_for(role))
            return

        if ui_menu == "admin_main":
            context.user_data["ui_menu"] = "main"
            await say(context, chat_id, "Меню", main_menu_for(role))
            return

    admin_mode = context.user_data.get("admin_mode")
    if is_adminish(role) and admin_mode:
        handled = await _handle_admin_mode(update, context, role, admin_mode, text)
        if handled:
            return

    if context.user_data.get("user_mode") == "add":
        context.user_data["user_mode"] = None
        limit = db.get_limit(chat_id)
        if db.client_count(chat_id) >= limit:
            await say(context, chat_id, f"Лимит {limit} конфигов исчерпан.", main_menu_for(role))
            return
        await create_client(context, chat_id, text)
        await say(context, chat_id, "Меню", main_menu_for(role))
        return

    if text == BUTTON_GUIDE:
        await say(context, chat_id, get_user_guide(), main_menu_for(role))
        return

    if text == BUTTON_ADD:
        context.user_data["user_mode"] = "add"
        await say(
            context,
            chat_id,
            "Введи название конфига",
            cancel_menu(),
        )
        return

    if text == BUTTON_LIST:
        rows = db.list_clients(chat_id)
        if not rows:
            await say(context, chat_id, "Список пуст.", main_menu_for(role))
            return
        items = [(display_name_for(chat_id, row["name"]), row["name"]) for row in rows]
        body = "\n".join(
            f"- {label} | {row['ip']} | регион: {db.region_label_by_code(row['region'])}" for (label, _), row in zip(items, rows)
        )
        await say(context, chat_id, f"Ваши конфиги:\n{body}", clients_kb(items))
        await say(context, chat_id, "Управление конфигами кнопками ниже сообщения. Основное меню под чатом.", main_menu_for(role))
        return

    if text == BUTTON_REGION:
        rows = db.list_clients(chat_id)
        if not rows:
            await say(context, chat_id, "У вас пока нет конфигов.", main_menu_for(role))
            return
        items = [
            (int(row["id"]), display_name_for(chat_id, row["name"]), db.region_label_by_code(row["region"]))
            for row in rows
        ]
        await say(context, chat_id, clients_region_text(rows), region_clients_kb(items))
        await say(context, chat_id, "Выбери конфиг, чтобы изменить регион выхода в интернет.", main_menu_for(role))
        return

    if text == BUTTON_SUPPORT or (ui_menu == "main" and ("вопрос" in lower or "поддерж" in lower)):
        await say(context, chat_id, get_support_text(), main_menu_for(role))
        return

    if is_adminish(role) and text == BUTTON_ADMIN:
        context.user_data["ui_menu"] = "admin_main"
        await say(context, chat_id, "Админка", admin_menu_for(role))
        return

    if is_adminish(role) and text == BUTTON_A_USERS:
        context.user_data["ui_menu"] = "admin_users"
        await say(context, chat_id, "Управление пользователями", admin_users_menu(is_super_owner(role)))
        return

    if is_adminish(role) and text == BUTTON_A_CLIENTS:
        context.user_data["ui_menu"] = "admin_main"
        context.user_data["admin_mode"] = "clients_owner"
        await say(context, chat_id, "Введи chat_id пользователя для просмотра его конфигов. Для отмены: 'Назад'.", admin_menu_for(role))
        return

    if is_adminish(role) and text == BUTTON_A_BROADCAST:
        context.user_data["ui_menu"] = "admin_main"
        context.user_data["admin_mode"] = "broadcast_targets"
        await say(context, chat_id, "Введи chat_id через пробел/запятую или 'all'. Для отмены: 'Назад'.", admin_menu_for(role))
        return

    if is_adminish(role) and text == BUTTON_A_LIMITS:
        context.user_data["ui_menu"] = "admin_main"
        context.user_data["admin_mode"] = "limit_target"
        await say(context, chat_id, "Введи chat_id пользователя для изменения лимита. Для отмены: 'Назад'.", admin_menu_for(role))
        return

    if is_adminish(role) and text == BUTTON_A_STATS:
        context.user_data["ui_menu"] = "admin_main"
        await send_stats(context, chat_id)
        await say(context, chat_id, "Админка", admin_menu_for(role))
        return

    if is_adminish(role) and text == BUTTON_A_LOGS:
        context.user_data["ui_menu"] = "admin_logs"
        await say(context, chat_id, "Логи", admin_logs_menu())
        return

    if is_adminish(role) and text == BUTTON_A_SYNC_PROFILES:
        context.user_data["ui_menu"] = "admin_main"
        await sync_profiles_from_telegram(context, chat_id)
        await say(context, chat_id, "Админка", admin_menu_for(role))
        return

    if is_adminish(role) and text == BUTTON_A_MONITORING:
        context.user_data["ui_menu"] = "admin_main"
        if MONITOR_URL:
            await say(context, chat_id, f"Мониторинг: {MONITOR_URL}", admin_menu_for(role))
        else:
            await say(context, chat_id, "MONITOR_URL не задан в .env", admin_menu_for(role))
        return

    if is_adminish(role) and text == BUTTON_A_CUSTOMIZE:
        context.user_data["ui_menu"] = "admin_customize"
        await say(context, chat_id, "Кастомизация", admin_customize_menu())
        return

    if is_adminish(role) and text == BUTTON_A_SERVERS:
        context.user_data["ui_menu"] = "admin_servers"
        await say(context, chat_id, "Управление серверами", admin_servers_menu())
        return

    if is_adminish(role) and text == BUTTON_S_LIST_IFACES:
        context.user_data["ui_menu"] = "admin_servers"
        await send_chunks(context, chat_id, list_interfaces_text(), admin_servers_menu())
        return

    if is_adminish(role) and text == BUTTON_S_LIST_REGIONS:
        context.user_data["ui_menu"] = "admin_servers"
        await send_chunks(context, chat_id, list_regions_text(), admin_servers_menu())
        return

    if is_adminish(role) and text == BUTTON_S_STATUS:
        context.user_data["ui_menu"] = "admin_servers"
        lines = ["Состояние интерфейсов:"]
        for iface in db.list_uplink_interfaces():
            ok, details = interface_status(iface["name"])
            lines.append(f"- {'OK' if ok else 'FAIL'} {details}")
        await send_chunks(context, chat_id, "\n".join(lines), admin_servers_menu())
        return

    if is_adminish(role) and text == BUTTON_S_ADD_IFACE:
        context.user_data["ui_menu"] = "admin_servers"
        context.user_data["admin_mode"] = "srv_add_iface"
        await say(
            context,
            chat_id,
            "Добавление интерфейса (канала выхода в интернет).\n\n"
            "Формат:\n"
            "<имя_интерфейса> <тип> [table_id]\n\n"
            "Где:\n"
            "- имя_интерфейса: например aw-de\n"
            "- тип:\n"
            "  amneziawg  — AmneziaWG интерфейс (обычно aw-*)\n"
            "  wireguard  — обычный WireGuard интерфейс\n"
            "  system     — системный интерфейс (например eth0)\n"
            "- table_id: номер таблицы маршрутизации (опционально)\n\n"
            "Примеры:\n"
            "aw-de amneziawg 210\n"
            "eth0 system",
            admin_servers_menu(),
        )
        return

    if is_adminish(role) and text == BUTTON_S_DEL_IFACE:
        context.user_data["ui_menu"] = "admin_servers"
        context.user_data["admin_mode"] = None
        body, names = interfaces_for_delete_text()
        if not names:
            await say(context, chat_id, body, admin_servers_menu())
            return
        await say(context, chat_id, body, servers_delete_iface_kb(names))
        await say(context, chat_id, "Для отмены нажми 'Назад'.", admin_servers_menu())
        return

    if is_adminish(role) and text == BUTTON_S_ADD_REGION:
        context.user_data["ui_menu"] = "admin_servers"
        context.user_data["admin_mode"] = "srv_add_region"
        await say(
            context,
            chat_id,
            "Добавление/изменение региона (то, что видит пользователь в кнопке «Регион»).\n\n"
            "Формат:\n"
            "<код>;<название>;<интерфейс>[;default]\n\n"
            "Где:\n"
            "- код: служебный ID региона (латиница), например germany\n"
            "- название: как показывать пользователю, например Германия\n"
            "- интерфейс: через какой интерфейс пускать трафик, например aw-de\n"
            "- default (опционально): сделать этот регион по умолчанию для новых конфигов\n\n"
            "Примеры:\n"
            "germany;Германия;aw-de\n"
            "latvia;Латвия;aw-lv;default",
            admin_servers_menu(),
        )
        return

    if is_adminish(role) and text == BUTTON_S_DEL_REGION:
        context.user_data["ui_menu"] = "admin_servers"
        context.user_data["admin_mode"] = None
        body, items = regions_for_delete_text()
        if not items:
            await say(context, chat_id, body, admin_servers_menu())
            return
        await say(context, chat_id, body, servers_delete_region_kb(items))
        await say(context, chat_id, "Для отмены нажми 'Назад'.", admin_servers_menu())
        return

    if is_adminish(role) and text == BUTTON_S_DEFAULT_REGION:
        context.user_data["ui_menu"] = "admin_servers"
        context.user_data["admin_mode"] = "srv_default_region"
        await say(
            context,
            chat_id,
            "Установка региона по умолчанию для НОВЫХ конфигов.\n"
            "Введи код региона, например: latvia",
            admin_servers_menu(),
        )
        return

    if is_adminish(role) and text == BUTTON_S_CFG_IFACE:
        context.user_data["ui_menu"] = "admin_servers"
        context.user_data["admin_mode"] = "srv_cfg_iface_name"
        await say(context, chat_id, "Введи имя интерфейса для замены конфига.", admin_servers_menu())
        return

    if is_adminish(role) and text == BUTTON_U_PENDING:
        context.user_data["ui_menu"] = "admin_users"
        await send_chunks(context, chat_id, format_users(db.users_by_role("pending")), admin_users_menu(is_super_owner(role)))
        return

    if is_adminish(role) and text == BUTTON_U_ACTIVE:
        context.user_data["ui_menu"] = "admin_users"
        rows = db.users_by_role("super_owner") + db.users_by_role("admin") + db.users_by_role("user")
        await send_chunks(context, chat_id, format_users(rows), admin_users_menu(is_super_owner(role)))
        return

    if is_adminish(role) and text == BUTTON_U_BANNED:
        context.user_data["ui_menu"] = "admin_users"
        await send_chunks(context, chat_id, format_users(db.users_by_role("banned")), admin_users_menu(is_super_owner(role)))
        return

    if is_adminish(role) and text == BUTTON_U_ADD:
        context.user_data["ui_menu"] = "admin_users"
        context.user_data["admin_mode"] = "add_user"
        await say(context, chat_id, "Введи chat_id для выдачи роли user. Для отмены: 'Назад'.", admin_users_menu(is_super_owner(role)))
        return

    if is_adminish(role) and text == BUTTON_U_BAN:
        context.user_data["ui_menu"] = "admin_users"
        context.user_data["admin_mode"] = "ban_target"
        await say(context, chat_id, "Введи chat_id для бана. Для отмены: 'Назад'.", admin_users_menu(is_super_owner(role)))
        return

    if is_adminish(role) and text == BUTTON_U_ADMINS:
        if not is_super_owner(role):
            await say(context, chat_id, "Недостаточно прав.", admin_users_menu(is_super_owner(role)))
            return
        rows = db.users_by_role("super_owner") + db.users_by_role("admin")
        context.user_data["ui_menu"] = "admin_users"
        await send_chunks(context, chat_id, format_users(rows), admin_users_menu(is_super_owner(role)))
        return

    if is_adminish(role) and text == BUTTON_U_PROMOTE:
        if not is_super_owner(role):
            await say(context, chat_id, "Недостаточно прав.", menu_for_ui(role, ui_menu))
            return
        context.user_data["admin_mode"] = "promote_admin"
        await say(context, chat_id, "Введи chat_id для назначения admin. Для отмены: 'Назад'.", menu_for_ui(role, ui_menu))
        return

    if is_adminish(role) and text == BUTTON_U_DEMOTE:
        if not is_super_owner(role):
            await say(context, chat_id, "Недостаточно прав.", menu_for_ui(role, ui_menu))
            return
        context.user_data["admin_mode"] = "demote_admin"
        await say(context, chat_id, "Введи chat_id для снятия роли admin. Для отмены: 'Назад'.", menu_for_ui(role, ui_menu))
        return

    if is_adminish(role) and text == BUTTON_L_RECENT:
        context.user_data["ui_menu"] = "admin_logs"
        await send_chunks(context, chat_id, format_logs(db.logs_recent(50)), admin_logs_menu())
        return

    if is_adminish(role) and text == BUTTON_L_BY_USER:
        context.user_data["ui_menu"] = "admin_logs"
        context.user_data["admin_mode"] = "logs_user"
        await say(context, chat_id, "Введи: chat_id и опционально лимит, например: 123456 200. Для отмены: 'Назад'.", admin_logs_menu())
        return

    if is_adminish(role) and text == BUTTON_L_CHAT_FILE:
        context.user_data["ui_menu"] = "admin_logs"
        context.user_data["admin_mode"] = "logs_chat_file"
        await say(context, chat_id, "Введи chat_id пользователя для скачивания чата. Для отмены: 'Назад'.", admin_logs_menu())
        return

    if is_adminish(role) and text == BUTTON_C_VIEW:
        context.user_data["ui_menu"] = "admin_customize"
        guide = get_user_guide()
        support = get_support_text()
        await send_chunks(context, chat_id, f"Текущая инструкция:\n{guide}\n\nТекущая поддержка:\n{support}", admin_customize_menu())
        return

    if is_adminish(role) and text == BUTTON_C_GUIDE:
        context.user_data["ui_menu"] = "admin_customize"
        context.user_data["admin_mode"] = "customize_guide"
        await say(context, chat_id, "Отправьте новый текст для раздела 'Инструкция'. Для отмены: 'Назад'.", admin_customize_menu())
        return

    if is_adminish(role) and text == BUTTON_C_SUPPORT:
        context.user_data["ui_menu"] = "admin_customize"
        context.user_data["admin_mode"] = "customize_support"
        await say(context, chat_id, "Отправьте новый текст для раздела 'Доступ / Вопросы'. Для отмены: 'Назад'.", admin_customize_menu())
        return

    await say(context, chat_id, "Меню", menu_for_ui(role, ui_menu))


async def _handle_admin_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    actor_role: str,
    admin_mode: str,
    text: str,
) -> bool:
    chat_id = str(update.effective_chat.id)
    ui_menu = context.user_data.get("ui_menu", "admin_main")

    if admin_mode == "add_user":
        target = text.strip()
        if not target:
            context.user_data["admin_mode"] = None
            await say(context, chat_id, "Пустой chat_id.")
            return True

        db.set_role(target, "user")
        db.log_event("role_set_user", chat_id, target, None)
        context.user_data["admin_mode"] = None
        await say(context, chat_id, f"Пользователь {target} получил роль user.", menu_for_ui(actor_role, ui_menu))
        try:
            chatlog.append(target, "bot", "Доступ одобрен. Используй /start")
            await context.bot.send_message(chat_id=int(target), text="Доступ одобрен. Используй /start")
        except Exception:
            pass
        return True

    if admin_mode == "promote_admin":
        if not is_super_owner(actor_role):
            context.user_data["admin_mode"] = None
            await say(context, chat_id, "Недостаточно прав.")
            return True
        target = text.strip()
        db.set_role(target, "admin")
        db.log_event("promote_admin", chat_id, target, None)
        context.user_data["admin_mode"] = None
        await say(context, chat_id, f"{target} теперь admin.", menu_for_ui(actor_role, ui_menu))
        return True

    if admin_mode == "demote_admin":
        if not is_super_owner(actor_role):
            context.user_data["admin_mode"] = None
            await say(context, chat_id, "Недостаточно прав.")
            return True

        target = text.strip()
        if db.role(target) == "super_owner":
            context.user_data["admin_mode"] = None
            await say(context, chat_id, "Нельзя понизить super_owner.")
            return True

        db.set_role(target, "user")
        db.log_event("demote_admin", chat_id, target, None)
        context.user_data["admin_mode"] = None
        await say(context, chat_id, f"{target} теперь user.", menu_for_ui(actor_role, ui_menu))
        return True

    if admin_mode == "broadcast_targets":
        raw = text.strip()
        if not raw:
            context.user_data["admin_mode"] = None
            await say(context, chat_id, "Ошибка: укажи chat_id через пробел или all.")
            return True

        if raw.lower() == "all":
            targets = db.approved_chat_ids()
        else:
            targets = [part for part in raw.replace(",", " ").split() if part]

        context.user_data["broadcast_targets"] = targets
        context.user_data["admin_mode"] = "broadcast_text"
        await say(context, chat_id, f"Введи текст рассылки. Получателей: {len(targets)}. Для отмены: 'Назад'.", menu_for_ui(actor_role, ui_menu))
        return True

    if admin_mode == "broadcast_text":
        targets = context.user_data.get("broadcast_targets", [])
        ok = 0
        bad = 0
        for target in targets:
            try:
                chatlog.append(str(target), "bot", text)
                await context.bot.send_message(chat_id=int(target), text=text)
                ok += 1
            except Exception:
                bad += 1

        context.user_data.pop("broadcast_targets", None)
        context.user_data["admin_mode"] = None
        db.log_event("broadcast", chat_id, None, f"targets={len(targets)} ok={ok} bad={bad}")
        await say(context, chat_id, f"Рассылка завершена. Успешно: {ok}, ошибок: {bad}.", menu_for_ui(actor_role, ui_menu))
        return True

    if admin_mode == "ban_target":
        context.user_data["ban_target"] = text.strip()
        context.user_data["admin_mode"] = "ban_reason"
        await say(context, chat_id, "Введи комментарий к бану. Для отмены: 'Назад'.", menu_for_ui(actor_role, ui_menu))
        return True

    if admin_mode == "ban_reason":
        target = context.user_data.get("ban_target", "")
        reason = text.strip() or "Не указан"

        if db.role(target) == "super_owner" and not (is_super_owner(actor_role) and target == chat_id):
            context.user_data["admin_mode"] = None
            context.user_data.pop("ban_target", None)
            await say(context, chat_id, "Нельзя забанить super_owner.")
            return True

        db.set_role(target, "banned")
        removed = purge_user_clients(target)
        db.log_event("ban", chat_id, target, f"reason={reason} removed={removed}")
        context.user_data["admin_mode"] = None
        context.user_data.pop("ban_target", None)

        await say(context, chat_id, f"Пользователь {target} забанен. Удалено конфигов: {removed}", menu_for_ui(actor_role, ui_menu))
        try:
            chatlog.append(target, "bot", f"Доступ заблокирован. Причина: {reason}")
            await context.bot.send_message(chat_id=int(target), text=f"Доступ заблокирован. Причина: {reason}")
        except Exception:
            pass
        return True

    if admin_mode == "limit_target":
        target = text.strip()
        current = db.get_limit(target)
        context.user_data["limit_target"] = target
        context.user_data["admin_mode"] = "limit_value"
        await say(context, chat_id, f"Текущий лимит: {current}. Введи новый лимит числом. Для отмены: 'Назад'.", menu_for_ui(actor_role, ui_menu))
        return True

    if admin_mode == "limit_value":
        target = context.user_data.get("limit_target", "")
        try:
            value = int(text.strip())
            if value < 0:
                raise ValueError
        except Exception:
            await say(context, chat_id, "Ошибка: введи неотрицательное число.")
            return True

        updated = db.set_limit(target, value)
        if not updated:
            context.user_data["admin_mode"] = None
            context.user_data.pop("limit_target", None)
            await say(context, chat_id, f"Пользователь {target} не найден.", menu_for_ui(actor_role, ui_menu))
            return True
        db.log_event("limit_set", chat_id, target, f"value={value}")
        context.user_data["admin_mode"] = None
        context.user_data.pop("limit_target", None)
        await say(context, chat_id, f"Лимит для {target}: {value}", menu_for_ui(actor_role, ui_menu))
        return True

    if admin_mode == "logs_user":
        parts = text.strip().split()
        if not parts:
            await say(context, chat_id, "Формат: <chat_id> [limit]")
            return True

        target = parts[0]
        limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 100
        rows = db.logs_for_user(target, limit)
        context.user_data["admin_mode"] = None
        await send_chunks(context, chat_id, format_logs(rows), admin_logs_menu())
        return True

    if admin_mode == "logs_chat_file":
        target_chat_id = text.strip()
        if not target_chat_id:
            await say(context, chat_id, "Укажи chat_id.")
            return True

        path = CHAT_DIR / f"{target_chat_id}.log"
        context.user_data["admin_mode"] = None
        if not path.exists():
            await say(context, chat_id, f"Файл чата не найден: {target_chat_id}.", admin_logs_menu())
            return True

        chatlog.append(chat_id, "bot", f"[document] chat/{target_chat_id}.log")
        with path.open("rb") as f:
            await context.bot.send_document(chat_id=int(chat_id), document=f, filename=f"{target_chat_id}.log")
        await say(context, chat_id, "Готово.", admin_logs_menu())
        return True

    if admin_mode == "clients_owner":
        target_owner = text.strip()
        rows = db.list_clients(target_owner)
        context.user_data["admin_mode"] = None
        if not rows:
            await say(context, chat_id, f"У пользователя {target_owner} нет конфигов.", menu_for_ui(actor_role, ui_menu))
            return True

        items = [(display_name_for(target_owner, r["name"]), r["name"]) for r in rows]
        body = "\n".join(
            f"- {label} | {r['ip']} | регион: {db.region_label_by_code(r['region'])}" for (label, _), r in zip(items, rows)
        )
        await say(context, chat_id, f"Конфиги пользователя {target_owner}:\n{body}", admin_user_clients_kb(target_owner, items))
        return True

    if admin_mode == "customize_guide":
        new_text = text.strip()
        if len(new_text) < 10:
            await say(context, chat_id, "Слишком короткий текст. Минимум 10 символов.")
            return True
        db.set_bot_text("user_guide", new_text)
        db.log_event("customize_user_guide", chat_id, None, f"len={len(new_text)}")
        context.user_data["admin_mode"] = None
        await say(context, chat_id, "Инструкция обновлена.", admin_customize_menu())
        return True

    if admin_mode == "customize_support":
        new_text = text.strip()
        if len(new_text) < 3:
            await say(context, chat_id, "Слишком короткий текст.")
            return True
        db.set_bot_text("support_text", new_text)
        db.log_event("customize_support_text", chat_id, None, f"len={len(new_text)}")
        context.user_data["admin_mode"] = None
        await say(context, chat_id, "Текст поддержки обновлен.", admin_customize_menu())
        return True

    if admin_mode == "srv_add_iface":
        parts = text.strip().split()
        if len(parts) < 2:
            await say(context, chat_id, "Неверный формат. Ожидаю: <ifname> <kind> [table_id].")
            return True
        ifname, kind = parts[0], parts[1]
        table_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        try:
            add_interface(ifname, kind=kind, table_id=table_id)
            db.log_event("server_add_interface", chat_id, None, f"{ifname} kind={kind} table_id={table_id}")
            context.user_data["admin_mode"] = None
            await say(context, chat_id, f"Интерфейс {ifname} сохранен.", admin_servers_menu())
            await send_chunks(context, chat_id, list_interfaces_text(), admin_servers_menu())
        except Exception as exc:
            await say(context, chat_id, f"Ошибка добавления интерфейса: {exc}", admin_servers_menu())
        return True

    if admin_mode == "srv_del_iface":
        ifname = text.strip()
        try:
            ok = delete_interface(ifname)
            context.user_data["admin_mode"] = None
            if ok:
                db.log_event("server_delete_interface", chat_id, None, ifname)
                await say(context, chat_id, f"Интерфейс {ifname} удален.", admin_servers_menu())
            else:
                await say(context, chat_id, "Не удалось удалить интерфейс (возможно, используется регионом).", admin_servers_menu())
        except Exception as exc:
            await say(context, chat_id, f"Ошибка удаления интерфейса: {exc}", admin_servers_menu())
        return True

    if admin_mode == "srv_add_region":
        parts = [x.strip() for x in text.split(";")]
        if len(parts) < 3:
            await say(context, chat_id, "Неверный формат. Ожидаю: <code>;<label>;<iface>[;default].")
            return True
        code, label, iface = parts[0], parts[1], parts[2]
        make_default = len(parts) > 3 and parts[3].strip().lower() in ("default", "1", "yes", "true")
        try:
            add_or_update_region(code, label, iface, is_default=make_default)
            db.log_event("server_upsert_region", chat_id, None, f"{code}->{iface} default={make_default}")
            context.user_data["admin_mode"] = None
            await say(context, chat_id, "Регион сохранен.", admin_servers_menu())
            await send_chunks(context, chat_id, list_regions_text(), admin_servers_menu())
        except Exception as exc:
            await say(context, chat_id, f"Ошибка сохранения региона: {exc}", admin_servers_menu())
        return True

    if admin_mode == "srv_del_region":
        parts = [x.strip() for x in text.split(";")]
        code = parts[0] if parts else ""
        move_to = parts[1] if len(parts) > 1 and parts[1] else None
        try:
            ok = remove_region(code, move_to)
            context.user_data["admin_mode"] = None
            if ok:
                db.log_event("server_delete_region", chat_id, None, f"{code} move_to={move_to}")
                await say(context, chat_id, "Регион удален.", admin_servers_menu())
            else:
                await say(context, chat_id, "Не удалось удалить регион.", admin_servers_menu())
            await send_chunks(context, chat_id, list_regions_text(), admin_servers_menu())
        except Exception as exc:
            await say(context, chat_id, f"Ошибка удаления региона: {exc}", admin_servers_menu())
        return True

    if admin_mode == "srv_default_region":
        code = text.strip()
        try:
            ok = set_default_region(code)
            context.user_data["admin_mode"] = None
            if ok:
                db.log_event("server_default_region", chat_id, None, code)
                await say(context, chat_id, f"Регион по умолчанию: {code}.", admin_servers_menu())
            else:
                await say(context, chat_id, "Регион не найден.", admin_servers_menu())
            await send_chunks(context, chat_id, list_regions_text(), admin_servers_menu())
        except Exception as exc:
            await say(context, chat_id, f"Ошибка установки default региона: {exc}", admin_servers_menu())
        return True

    if admin_mode == "srv_cfg_iface_name":
        ifname = text.strip()
        iface = db.get_uplink_interface(ifname)
        if not iface:
            await say(context, chat_id, "Интерфейс не найден.", admin_servers_menu())
            return True
        context.user_data["srv_iface_for_cfg"] = ifname
        context.user_data["admin_mode"] = "srv_cfg_iface_body"
        await say(
            context,
            chat_id,
            f"Замена конфига интерфейса {ifname}.\n\n"
            "Отправь полный новый конфиг одним сообщением.\n"
            "Система сделает бэкап, попробует применить и при ошибке откатит обратно.",
            admin_servers_menu(),
        )
        return True

    if admin_mode == "srv_cfg_iface_body":
        ifname = str(context.user_data.get("srv_iface_for_cfg", "")).strip()
        ok, msg = replace_interface_config(ifname, text)
        context.user_data.pop("srv_iface_for_cfg", None)
        context.user_data["admin_mode"] = None
        db.log_event("server_replace_config", chat_id, None, f"{ifname}: {msg}")
        await say(context, chat_id, msg, admin_servers_menu())
        return True

    return False


async def on_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    chat_id = sync_user_profile(update)
    role = db.role(chat_id)
    data = query.data
    chatlog.append(chat_id, "user", f"[inline] {data}")

    if data.startswith("send:"):
        stored_name = data.split("send:", 1)[1]
        if not db.get_client(chat_id, stored_name):
            await say(context, chat_id, "Отправка запрещена.")
            return
        await send_client(context, chat_id, stored_name)
        return

    if data.startswith("del:"):
        stored_name = data.split("del:", 1)[1]
        if not db.get_client(chat_id, stored_name):
            await say(context, chat_id, "Удаление запрещено.")
            return
        await revoke_client(context, chat_id, stored_name)
        return

    if data == "rlist":
        rows = db.list_clients(chat_id)
        if not rows:
            await say(context, chat_id, "У вас пока нет конфигов.")
            return
        items = [
            (int(row["id"]), display_name_for(chat_id, row["name"]), db.region_label_by_code(row["region"]))
            for row in rows
        ]
        await say(context, chat_id, clients_region_text(rows), region_clients_kb(items))
        return

    if data.startswith("rsel:"):
        client_id_raw = data.split(":", 1)[1].strip()
        if not client_id_raw.isdigit():
            await say(context, chat_id, "Некорректный выбор.")
            return
        row = db.get_client_by_id(chat_id, int(client_id_raw))
        if not row:
            await say(context, chat_id, "Конфиг не найден.")
            return
        label = display_name_for(chat_id, row["name"])
        options = [(r["code"], r["label"]) for r in db.list_regions()]
        current_code = row["region"]
        await say(
            context,
            chat_id,
            f"Конфиг: {label}\nIP: {row['ip']}\nТекущий регион: {db.region_label_by_code(current_code)}\nВыбери новый регион:",
            region_pick_kb(int(row["id"]), options, current_code),
        )
        return

    if data.startswith("rset:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            await say(context, chat_id, "Некорректный выбор.")
            return
        _, client_id_raw, region_code = parts
        if not client_id_raw.isdigit():
            await say(context, chat_id, "Некорректный выбор.")
            return
        row = db.get_client_by_id(chat_id, int(client_id_raw))
        if not row:
            await say(context, chat_id, "Конфиг не найден.")
            return
        region_code = normalize_region(region_code)
        if not db.set_client_region(chat_id, int(client_id_raw), region_code):
            await say(context, chat_id, "Не удалось изменить регион.")
            return
        try:
            sync_client_egress_routes()
        except Exception as exc:
            db.log_event("region_sync_error", chat_id, chat_id, str(exc))
            await say(context, chat_id, "Регион сохранен, но применение маршрутизации завершилось с ошибкой.")
            return

        label = display_name_for(chat_id, row["name"])
        db.log_event(
            "client_region_set",
            chat_id,
            chat_id,
            f"name={row['name']} ip={row['ip']} old_region={row['region']} new_region={region_code}",
        )
        await say(context, chat_id, f"Готово: {label} теперь выходит через регион {db.region_label_by_code(region_code)}.")
        rows = db.list_clients(chat_id)
        items = [
            (int(item["id"]), display_name_for(chat_id, item["name"]), db.region_label_by_code(item["region"]))
            for item in rows
        ]
        await say(context, chat_id, clients_region_text(rows), region_clients_kb(items))
        return

    if not is_adminish(role):
        await say(context, chat_id, "Недостаточно прав.")
        return

    if data == "srv_back":
        context.user_data["ui_menu"] = "admin_servers"
        context.user_data["admin_mode"] = None
        await say(context, chat_id, "Управление серверами", admin_servers_menu())
        return

    if data.startswith("sdelif:"):
        ifname = data.split(":", 1)[1].strip()
        if not ifname:
            await say(context, chat_id, "Некорректный интерфейс.", admin_servers_menu())
            return
        try:
            ok = delete_interface(ifname)
            if ok:
                db.log_event("server_delete_interface", chat_id, None, ifname)
                await say(context, chat_id, f"Интерфейс {ifname} удален.", admin_servers_menu())
            else:
                await say(context, chat_id, "Не удалось удалить интерфейс (возможно, используется регионом).", admin_servers_menu())
        except Exception as exc:
            await say(context, chat_id, f"Ошибка удаления интерфейса: {exc}", admin_servers_menu())
            return

        body, names = interfaces_for_delete_text()
        if names:
            await say(context, chat_id, body, servers_delete_iface_kb(names))
        else:
            await say(context, chat_id, body, admin_servers_menu())
        return

    if data.startswith("sdelrg:"):
        code = data.split(":", 1)[1].strip()
        if not code:
            await say(context, chat_id, "Некорректный регион.", admin_servers_menu())
            return
        try:
            ok = remove_region(code, None)
            if ok:
                db.log_event("server_delete_region", chat_id, None, f"{code} move_to=default")
                await say(context, chat_id, f"Регион {code} удален.", admin_servers_menu())
            else:
                await say(context, chat_id, "Не удалось удалить регион.", admin_servers_menu())
        except Exception as exc:
            await say(context, chat_id, f"Ошибка удаления региона: {exc}", admin_servers_menu())
            return

        body, items = regions_for_delete_text()
        if items:
            await say(context, chat_id, body, servers_delete_region_kb(items))
        else:
            await say(context, chat_id, body, admin_servers_menu())
        return

    if data == "a_stats":
        await send_stats(context, chat_id)
        await say(context, chat_id, "Админка", admin_menu_for(role))
        return

    if data == "a_sync_profiles":
        await sync_profiles_from_telegram(context, chat_id)
        await say(context, chat_id, "Админка", admin_menu_for(role))
        return

    if data == "a_customize":
        context.user_data["ui_menu"] = "admin_customize"
        await say(context, chat_id, "Кастомизация", admin_customize_menu())
        return

    if data == "a_monitoring":
        if MONITOR_URL:
            await say(context, chat_id, f"Мониторинг: {MONITOR_URL}", admin_menu_for(role))
        else:
            await say(context, chat_id, "MONITOR_URL не задан в .env", admin_menu_for(role))
        return

    if data == "a_logs":
        context.user_data["ui_menu"] = "admin_logs"
        await say(context, chat_id, "Выбери режим логов", admin_logs_menu())
        return

    if data == "a_logs_recent":
        context.user_data["ui_menu"] = "admin_logs"
        await send_chunks(context, chat_id, format_logs(db.logs_recent(50)), admin_logs_menu())
        return

    if data == "a_logs_user":
        context.user_data["ui_menu"] = "admin_logs"
        context.user_data["admin_mode"] = "logs_user"
        await say(context, chat_id, "Введи: chat_id и опционально лимит, например: 123456 200. Для отмены: 'Назад'.", admin_logs_menu())
        return

    if data == "a_logs_chat_file":
        context.user_data["ui_menu"] = "admin_logs"
        context.user_data["admin_mode"] = "logs_chat_file"
        await say(context, chat_id, "Введи chat_id пользователя для скачивания чата. Для отмены: 'Назад'.", admin_logs_menu())
        return

    if data == "u_users":
        context.user_data["ui_menu"] = "admin_users"
        await say(context, chat_id, "Управление пользователями", admin_users_menu(is_super_owner(role)))
        return

    if data == "u_pending":
        await send_chunks(context, chat_id, format_users(db.users_by_role("pending")), admin_users_menu(is_super_owner(role)))
        return

    if data == "u_active":
        rows = db.users_by_role("super_owner") + db.users_by_role("admin") + db.users_by_role("user")
        await send_chunks(context, chat_id, format_users(rows), admin_users_menu(is_super_owner(role)))
        return

    if data == "u_banned":
        await send_chunks(context, chat_id, format_users(db.users_by_role("banned")), admin_users_menu(is_super_owner(role)))
        return

    if data == "u_add":
        context.user_data["ui_menu"] = "admin_users"
        context.user_data["admin_mode"] = "add_user"
        await say(context, chat_id, "Введи chat_id для выдачи роли user. Для отмены: 'Назад'.", admin_users_menu(is_super_owner(role)))
        return

    if data == "u_ban":
        context.user_data["ui_menu"] = "admin_users"
        context.user_data["admin_mode"] = "ban_target"
        await say(context, chat_id, "Введи chat_id для бана. Для отмены: 'Назад'.", admin_users_menu(is_super_owner(role)))
        return

    if data == "u_clients":
        context.user_data["ui_menu"] = "admin_main"
        context.user_data["admin_mode"] = "clients_owner"
        await say(context, chat_id, "Введи chat_id пользователя для просмотра его конфигов. Для отмены: 'Назад'.", admin_menu_for(role))
        return

    if data == "u_broadcast":
        context.user_data["ui_menu"] = "admin_main"
        context.user_data["admin_mode"] = "broadcast_targets"
        await say(context, chat_id, "Введи chat_id через пробел/запятую или 'all'. Для отмены: 'Назад'.", admin_menu_for(role))
        return

    if data == "u_limit":
        context.user_data["ui_menu"] = "admin_main"
        context.user_data["admin_mode"] = "limit_target"
        await say(context, chat_id, "Введи chat_id пользователя для изменения лимита. Для отмены: 'Назад'.", admin_menu_for(role))
        return

    if data == "u_admins":
        if not is_super_owner(role):
            await say(context, chat_id, "Недостаточно прав.")
            return
        rows = db.users_by_role("super_owner") + db.users_by_role("admin")
        await send_chunks(context, chat_id, format_users(rows), admin_users_menu(is_super_owner(role)))
        return

    if data == "u_promote":
        if not is_super_owner(role):
            await say(context, chat_id, "Недостаточно прав.")
            return
        context.user_data["ui_menu"] = "admin_main"
        context.user_data["admin_mode"] = "promote_admin"
        await say(context, chat_id, "Введи chat_id для назначения admin. Для отмены: 'Назад'.", admin_menu_for(role))
        return

    if data == "u_demote":
        if not is_super_owner(role):
            await say(context, chat_id, "Недостаточно прав.")
            return
        context.user_data["ui_menu"] = "admin_main"
        context.user_data["admin_mode"] = "demote_admin"
        await say(context, chat_id, "Введи chat_id для снятия роли admin. Для отмены: 'Назад'.", admin_menu_for(role))
        return

    if data == "u_back_main":
        context.user_data["ui_menu"] = "admin_main"
        await say(context, chat_id, "Админка", admin_menu_for(role))
        return

    if data == "back":
        context.user_data.clear()
        await start(update, context)
        return

    if data.startswith("asend:"):
        _, owner_id, stored_name = data.split(":", 2)
        await send_client_to_admin(context, owner_id, stored_name, chat_id)
        return

    if data.startswith("adel:"):
        _, owner_id, stored_name = data.split(":", 2)
        await revoke_client(context, owner_id, stored_name)
        await say(context, chat_id, f"Удалено у {owner_id}: {display_name_for(owner_id, stored_name)}")
        return


async def send_stats(context: ContextTypes.DEFAULT_TYPE, chat_id: str) -> None:
    stats = db.stats()

    net = ipaddress.ip_network(VPN_SUBNET, strict=False)
    used = set(stats.get("used_ips", []))
    first_host = next(net.hosts(), None)
    if first_host:
        used.add(str(first_host))

    total_hosts = sum(1 for _ in net.hosts())
    used_count = len(used)
    free_count = max(total_hosts - used_count, 0)

    header = (
        "Статистика:\n"
        f"- Пользователей всего: {stats['total_users']}\n"
        f"- super_owner: {stats['role_super_owner']}  admin: {stats['role_admin']}  user: {stats['role_user']}\n"
        f"- pending: {stats['role_pending']}  banned: {stats['role_banned']}\n"
        f"- Конфигов всего: {stats['total_clients']}\n"
        f"- Подсеть: {VPN_SUBNET}\n"
        f"- IP занято: {used_count}  свободно: {free_count}\n"
        "Занятые IP:\n"
    )

    def _sort_key(ip: str):
        return tuple(int(x) for x in ip.split(".")) if "." in ip else (ip,)

    body = "\n".join(sorted(used, key=_sort_key)) if used else "(нет)"
    await send_chunks(context, chat_id, header + body)


async def sync_profiles_from_telegram(context: ContextTypes.DEFAULT_TYPE, actor_chat_id: str) -> None:
    rows = db.all_users()
    ok = 0
    failed = 0

    for row in rows:
        target_chat_id = str(row["chat_id"])
        try:
            chat = await context.bot.get_chat(chat_id=int(target_chat_id))
            db.upsert_user(
                target_chat_id,
                getattr(chat, "username", None),
                getattr(chat, "first_name", None),
                getattr(chat, "last_name", None),
            )
            ok += 1
        except Exception:
            failed += 1

    db.log_event("profiles_sync", actor_chat_id, None, f"ok={ok} failed={failed}")
    await say(context, actor_chat_id, f"Синхронизация завершена. Обновлено: {ok}, ошибок: {failed}.")


def register_handlers(app) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CallbackQueryHandler(on_inline))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
