from __future__ import annotations

from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton


BUTTON_ADD = "Добавить"
BUTTON_LIST = "Список"
BUTTON_GUIDE = "Инструкция"
BUTTON_SUPPORT = "Вопросы / Поддержка"
BUTTON_ADMIN = "Админка"
BUTTON_BACK = "Назад"

BUTTON_A_USERS = "Пользователи"
BUTTON_A_CLIENTS = "Конфиги пользователей"
BUTTON_A_SYNC_PROFILES = "Синхронизировать профили"
BUTTON_A_MONITORING = "Мониторинг"
BUTTON_A_CUSTOMIZE = "Кастомизация"
BUTTON_A_BROADCAST = "Сообщения"
BUTTON_A_LIMITS = "Лимиты"
BUTTON_A_STATS = "Статистика"
BUTTON_A_LOGS = "Логи"

BUTTON_U_PENDING = "Заявки"
BUTTON_U_ACTIVE = "Активные"
BUTTON_U_BANNED = "Баны"
BUTTON_U_ADD = "Выдать доступ"
BUTTON_U_BAN = "Забанить"
BUTTON_U_ADMINS = "Администраторы"
BUTTON_U_PROMOTE = "Назначить админом"
BUTTON_U_DEMOTE = "Снять админа"

BUTTON_L_RECENT = "Последние 50"
BUTTON_L_BY_USER = "По chat_id"
BUTTON_L_CHAT_FILE = "Скачать чат"

BUTTON_P_STATUS = "Проверить статус"
BUTTON_P_SUPPORT = "Доступ / Вопросы"

BUTTON_C_VIEW = "Показать тексты"
BUTTON_C_GUIDE = "Изменить инструкцию"
BUTTON_C_SUPPORT = "Изменить поддержку"


def bottom_menu(is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [BUTTON_ADD, BUTTON_LIST],
        [BUTTON_GUIDE, BUTTON_SUPPORT],
    ]
    if is_admin:
        rows.append([BUTTON_ADMIN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_main_menu(is_super_owner: bool) -> ReplyKeyboardMarkup:
    rows = [
        [BUTTON_A_USERS, BUTTON_A_CLIENTS],
        [BUTTON_A_MONITORING, BUTTON_A_CUSTOMIZE],
        [BUTTON_A_BROADCAST, BUTTON_A_LIMITS],
        [BUTTON_A_STATS, BUTTON_A_LOGS],
        [BUTTON_A_SYNC_PROFILES],
    ]
    rows.append([BUTTON_BACK])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_users_menu(is_super_owner: bool) -> ReplyKeyboardMarkup:
    rows = [
        [BUTTON_U_PENDING, BUTTON_U_ACTIVE, BUTTON_U_BANNED],
        [BUTTON_U_ADD, BUTTON_U_BAN],
    ]
    if is_super_owner:
        rows.append([BUTTON_U_ADMINS, BUTTON_U_PROMOTE, BUTTON_U_DEMOTE])
    rows.append([BUTTON_BACK])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_logs_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BUTTON_L_RECENT, BUTTON_L_BY_USER],
            [BUTTON_L_CHAT_FILE],
            [BUTTON_BACK],
        ],
        resize_keyboard=True,
    )


def pending_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BUTTON_P_STATUS, BUTTON_P_SUPPORT],
        ],
        resize_keyboard=True,
    )


def admin_customize_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BUTTON_C_VIEW],
            [BUTTON_C_GUIDE],
            [BUTTON_C_SUPPORT],
            [BUTTON_BACK],
        ],
        resize_keyboard=True,
    )


def clients_kb(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(f"Отправить {label}", callback_data=f"send:{stored_name}"),
            InlineKeyboardButton(f"Удалить {label}", callback_data=f"del:{stored_name}"),
        ]
        for label, stored_name in items
    ]
    return InlineKeyboardMarkup(rows)


def admin_main_kb(is_super_owner: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Пользователи", callback_data="u_users")],
        [InlineKeyboardButton("Конфиги пользователей", callback_data="u_clients")],
        [
            InlineKeyboardButton("Мониторинг", callback_data="a_monitoring"),
            InlineKeyboardButton("Кастомизация", callback_data="a_customize"),
        ],
        [InlineKeyboardButton("Синхронизировать профили", callback_data="a_sync_profiles")],
        [InlineKeyboardButton("Сообщения", callback_data="u_broadcast")],
        [InlineKeyboardButton("Лимиты", callback_data="u_limit")],
        [InlineKeyboardButton("Статистика", callback_data="a_stats")],
        [InlineKeyboardButton("Логи", callback_data="a_logs")],
    ]
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


def admin_user_clients_kb(owner_chat_id: str, items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(f"Отправить {label}", callback_data=f"asend:{owner_chat_id}:{stored_name}"),
            InlineKeyboardButton(f"Удалить {label}", callback_data=f"adel:{owner_chat_id}:{stored_name}"),
        ]
        for label, stored_name in items
    ]
    return InlineKeyboardMarkup(rows)


def logs_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Последние 50", callback_data="a_logs_recent")],
            [InlineKeyboardButton("По chat_id", callback_data="a_logs_user")],
            [InlineKeyboardButton("Скачать чат", callback_data="a_logs_chat_file")],
            [InlineKeyboardButton("Назад", callback_data="u_back_main")],
        ]
    )
