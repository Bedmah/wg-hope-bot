from __future__ import annotations

import ipaddress

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from . import db
from .actions import (
    say,
    create_client,
    send_client,
    revoke_client,
    send_client_to_admin,
    purge_user_clients,
    format_users,
    format_logs,
    USER_GUIDE,
    SUPPORT_TEXT,
)
from .keyboards import (
    bottom_menu,
    clients_kb,
    admin_main_kb,
    admin_users_kb,
    admin_user_clients_kb,
    logs_kb,
    BUTTON_ADD,
    BUTTON_LIST,
    BUTTON_GUIDE,
    BUTTON_SUPPORT,
    BUTTON_ADMIN,
)
from .settings import SUPER_OWNER_CHAT_ID, VPN_SUBNET


def is_adminish(role: str | None) -> bool:
    return role in ("super_owner", "admin")


def is_super_owner(role: str | None) -> bool:
    return role == "super_owner"


async def send_chunks(context: ContextTypes.DEFAULT_TYPE, chat_id: str, text: str, kb=None) -> None:
    chunk_size = 3900
    if len(text) <= chunk_size:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
        return

    pos = 0
    while pos < len(text):
        part = text[pos : pos + chunk_size]
        pos += chunk_size
        await context.bot.send_message(
            chat_id=chat_id,
            text=part,
            reply_markup=kb if pos >= len(text) else None,
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db.init()
    user = update.effective_user
    chat_id = str(update.effective_chat.id)

    db.upsert_user(chat_id, user.username if user else None, user.first_name if user else None, user.last_name if user else None)
    db.log_event("start", chat_id, chat_id, None)

    role = db.role(chat_id)
    if role in (None, "pending"):
        await say(context, chat_id, "Доступ запрошен. Ожидайте одобрения администратора.")
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
    await say(context, chat_id, "Меню", bottom_menu(is_adminish(role)))


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
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
    chat_id = str(update.effective_chat.id)
    db.touch_seen(chat_id)

    role = db.role(chat_id)
    if role not in ("super_owner", "admin", "user"):
        db.log_event("denied_message", chat_id, chat_id, None)
        await say(context, chat_id, "Нет доступа.")
        return

    text = (update.message.text or "").strip()
    lower = text.lower()

    admin_mode = context.user_data.get("admin_mode")
    if is_adminish(role) and admin_mode:
        handled = await _handle_admin_mode(update, context, role, admin_mode, text)
        if handled:
            return

    if context.user_data.get("user_mode") == "add":
        context.user_data["user_mode"] = None
        limit = db.get_limit(chat_id)
        if db.client_count(chat_id) >= limit:
            await say(context, chat_id, f"Лимит {limit} конфигов исчерпан.")
            return
        await create_client(context, chat_id, text)
        return

    if BUTTON_GUIDE.lower() in lower:
        await say(context, chat_id, USER_GUIDE)
        return

    if BUTTON_ADD.lower() in lower:
        context.user_data["user_mode"] = "add"
        await say(context, chat_id, "Введите название конфига (латиница/цифры, пробелы заменятся на _).")
        return

    if BUTTON_LIST.lower() in lower:
        rows = db.list_clients(chat_id)
        if not rows:
            await say(context, chat_id, "Список пуст.", bottom_menu(is_adminish(role)))
            return
        names = [row["name"] for row in rows]
        body = "\n".join(f"- {row['name']} | {row['ip']}" for row in rows)
        await say(context, chat_id, f"Ваши конфиги:\n{body}", clients_kb(names))
        await say(context, chat_id, "Меню", bottom_menu(is_adminish(role)))
        return

    if "вопрос" in lower or "поддерж" in lower or BUTTON_SUPPORT.lower() in lower:
        await say(context, chat_id, SUPPORT_TEXT)
        return

    if is_adminish(role) and BUTTON_ADMIN.lower() in lower:
        await say(context, chat_id, "Админка", admin_main_kb(is_super_owner(role)))
        return

    await say(context, chat_id, "Меню", bottom_menu(is_adminish(role)))


async def _handle_admin_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    actor_role: str,
    admin_mode: str,
    text: str,
) -> bool:
    chat_id = str(update.effective_chat.id)

    if admin_mode == "add_user":
        target = text.strip()
        if not target:
            context.user_data["admin_mode"] = None
            await say(context, chat_id, "Пустой chat_id.")
            return True

        db.set_role(target, "user")
        db.log_event("role_set_user", chat_id, target, None)
        context.user_data["admin_mode"] = None
        await say(context, chat_id, f"Пользователь {target} получил роль user.")
        try:
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
        await say(context, chat_id, f"{target} теперь admin.")
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
        await say(context, chat_id, f"{target} теперь user.")
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
        await say(context, chat_id, f"Введи текст рассылки. Получателей: {len(targets)}")
        return True

    if admin_mode == "broadcast_text":
        targets = context.user_data.get("broadcast_targets", [])
        ok = 0
        bad = 0
        for target in targets:
            try:
                await context.bot.send_message(chat_id=int(target), text=text)
                ok += 1
            except Exception:
                bad += 1

        context.user_data.pop("broadcast_targets", None)
        context.user_data["admin_mode"] = None
        db.log_event("broadcast", chat_id, None, f"targets={len(targets)} ok={ok} bad={bad}")
        await say(context, chat_id, f"Рассылка завершена. Успешно: {ok}, ошибок: {bad}.")
        return True

    if admin_mode == "ban_target":
        context.user_data["ban_target"] = text.strip()
        context.user_data["admin_mode"] = "ban_reason"
        await say(context, chat_id, "Введи комментарий к бану.")
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

        await say(context, chat_id, f"Пользователь {target} забанен. Удалено конфигов: {removed}")
        try:
            await context.bot.send_message(chat_id=int(target), text=f"Доступ заблокирован. Причина: {reason}")
        except Exception:
            pass
        return True

    if admin_mode == "limit_target":
        target = text.strip()
        current = db.get_limit(target)
        context.user_data["limit_target"] = target
        context.user_data["admin_mode"] = "limit_value"
        await say(context, chat_id, f"Текущий лимит: {current}. Введи новый лимит числом.")
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

        db.set_limit(target, value)
        db.log_event("limit_set", chat_id, target, f"value={value}")
        context.user_data["admin_mode"] = None
        context.user_data.pop("limit_target", None)
        await say(context, chat_id, f"Лимит для {target}: {value}")
        return True

    if admin_mode == "logs_user":
        parts = text.strip().split()
        if not parts:
            await say(context, chat_id, "Формат: <chat_id> [limit]")
            return True

        target = parts[0]
        limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 100
        rows = db.logs_for_user(target, limit)
        await send_chunks(context, chat_id, format_logs(rows), logs_kb())
        return True

    if admin_mode == "clients_owner":
        target_owner = text.strip()
        rows = db.list_clients(target_owner)
        context.user_data["admin_mode"] = None
        if not rows:
            await say(context, chat_id, f"У пользователя {target_owner} нет конфигов.")
            return True

        names = [r["name"] for r in rows]
        body = "\n".join(f"- {r['name']} | {r['ip']}" for r in rows)
        await say(context, chat_id, f"Конфиги пользователя {target_owner}:\n{body}", admin_user_clients_kb(target_owner, names))
        return True

    return False


async def on_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    chat_id = str(query.message.chat_id)
    role = db.role(chat_id)
    data = query.data

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

    if not is_adminish(role):
        await say(context, chat_id, "Недостаточно прав.")
        return

    if data == "a_stats":
        await send_stats(context, chat_id)
        return

    if data == "a_logs":
        await say(context, chat_id, "Выбери режим логов", logs_kb())
        return

    if data == "a_logs_recent":
        await send_chunks(context, chat_id, format_logs(db.logs_recent(50)), logs_kb())
        return

    if data == "a_logs_user":
        context.user_data["admin_mode"] = "logs_user"
        await say(context, chat_id, "Введи: chat_id и опционально лимит, например: 123456 200")
        return

    if data == "u_users":
        await say(context, chat_id, "Управление пользователями", admin_users_kb())
        return

    if data == "u_pending":
        await send_chunks(context, chat_id, format_users(db.users_by_role("pending")))
        return

    if data == "u_active":
        rows = db.users_by_role("super_owner") + db.users_by_role("admin") + db.users_by_role("user")
        await send_chunks(context, chat_id, format_users(rows))
        return

    if data == "u_banned":
        await send_chunks(context, chat_id, format_users(db.users_by_role("banned")))
        return

    if data == "u_add":
        context.user_data["admin_mode"] = "add_user"
        await say(context, chat_id, "Введи chat_id для выдачи роли user.")
        return

    if data == "u_ban":
        context.user_data["admin_mode"] = "ban_target"
        await say(context, chat_id, "Введи chat_id для бана.")
        return

    if data == "u_clients":
        context.user_data["admin_mode"] = "clients_owner"
        await say(context, chat_id, "Введи chat_id пользователя для просмотра его конфигов.")
        return

    if data == "u_broadcast":
        context.user_data["admin_mode"] = "broadcast_targets"
        await say(context, chat_id, "Введи chat_id через пробел/запятую или 'all'.")
        return

    if data == "u_limit":
        context.user_data["admin_mode"] = "limit_target"
        await say(context, chat_id, "Введи chat_id пользователя для изменения лимита.")
        return

    if data == "u_admins":
        if not is_super_owner(role):
            await say(context, chat_id, "Недостаточно прав.")
            return
        rows = db.users_by_role("super_owner") + db.users_by_role("admin")
        await send_chunks(context, chat_id, format_users(rows))
        return

    if data == "u_promote":
        if not is_super_owner(role):
            await say(context, chat_id, "Недостаточно прав.")
            return
        context.user_data["admin_mode"] = "promote_admin"
        await say(context, chat_id, "Введи chat_id для назначения admin.")
        return

    if data == "u_demote":
        if not is_super_owner(role):
            await say(context, chat_id, "Недостаточно прав.")
            return
        context.user_data["admin_mode"] = "demote_admin"
        await say(context, chat_id, "Введи chat_id для снятия роли admin.")
        return

    if data == "u_back_main":
        await say(context, chat_id, "Админка", admin_main_kb(is_super_owner(role)))
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
        await say(context, chat_id, f"Удалено у {owner_id}: {stored_name}")
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


def register_handlers(app) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CallbackQueryHandler(on_inline))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
