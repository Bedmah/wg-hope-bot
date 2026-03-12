from __future__ import annotations

from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton


BUTTON_ADD = "Добавить"
BUTTON_LIST = "Список"
BUTTON_GUIDE = "Инструкция"
BUTTON_SUPPORT = "Вопросы / Поддержка"
BUTTON_ADMIN = "Админка"


def bottom_menu(is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [BUTTON_ADD, BUTTON_LIST],
        [BUTTON_GUIDE, BUTTON_SUPPORT],
    ]
    if is_admin:
        rows.append([BUTTON_ADMIN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def clients_kb(names: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(f"Отправить {name}", callback_data=f"send:{name}"),
            InlineKeyboardButton(f"Удалить {name}", callback_data=f"del:{name}"),
        ]
        for name in names
    ]
    return InlineKeyboardMarkup(rows)


def admin_main_kb(is_super_owner: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Пользователи", callback_data="u_users")],
        [InlineKeyboardButton("Конфиги пользователей", callback_data="u_clients")],
        [InlineKeyboardButton("Сообщения", callback_data="u_broadcast")],
        [InlineKeyboardButton("Лимиты", callback_data="u_limit")],
        [InlineKeyboardButton("Статистика", callback_data="a_stats")],
        [InlineKeyboardButton("Логи", callback_data="a_logs")],
    ]
    if is_super_owner:
        rows.insert(
            1,
            [
                InlineKeyboardButton("Администраторы", callback_data="u_admins"),
                InlineKeyboardButton("Назначить админом", callback_data="u_promote"),
                InlineKeyboardButton("Снять админа", callback_data="u_demote"),
            ],
        )
    rows.append([InlineKeyboardButton("Назад", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def admin_users_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Заявки", callback_data="u_pending"),
                InlineKeyboardButton("Активные", callback_data="u_active"),
                InlineKeyboardButton("Баны", callback_data="u_banned"),
            ],
            [
                InlineKeyboardButton("Выдать доступ", callback_data="u_add"),
                InlineKeyboardButton("Забанить", callback_data="u_ban"),
            ],
            [InlineKeyboardButton("Назад", callback_data="u_back_main")],
        ]
    )


def admin_user_clients_kb(owner_chat_id: str, names: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(f"Отправить {name}", callback_data=f"asend:{owner_chat_id}:{name}"),
            InlineKeyboardButton(f"Удалить {name}", callback_data=f"adel:{owner_chat_id}:{name}"),
        ]
        for name in names
    ]
    return InlineKeyboardMarkup(rows)


def logs_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Последние 50", callback_data="a_logs_recent")],
            [InlineKeyboardButton("По chat_id", callback_data="a_logs_user")],
            [InlineKeyboardButton("Назад", callback_data="u_back_main")],
        ]
    )
