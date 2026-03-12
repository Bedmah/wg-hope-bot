# WG Hope Bot

Telegram-бот для управления доступом к WireGuard + веб-мониторинг пользователей и конфигов.

## Релизы

- `v1.1.0` - бот + веб-мониторинг, кастомизация текстов, логи чатов, улучшенная админка
- `v1.0.0` - рефакторинг архитектуры и ролевая модель
- `v0.0.1` - историческая базовая версия

## Что умеет проект

- Роли: `super_owner`, `admin`, `user`, `pending`, `banned`
- Выдача WireGuard-конфигов (`.conf` + QR)
- Лимиты конфигов, рассылки, логи и статистика
- Одобрение/бан пользователей через админку
- Синхронизация профилей Telegram (username/имя)
- Хранение истории чатов в `chat/<chat_id>.log`
- Веб-мониторинг с графиками трафика, скорости и handshake
- Фильтры мониторинга по периоду, конфигу и типу событий
- Кастомизация текста разделов `Инструкция` и `Вопросы / Поддержка` через админку

## Структура

- `vpn_bot/` - логика Telegram-бота
- `monitor/` - FastAPI веб-мониторинг
- `chat/` - история чатов пользователей
- `bot.py` - точка входа бота
- `CHANGELOG.md` - история релизов

## Требования

- Linux сервер
- Python 3.11+
- WireGuard (`wg`, `wg-quick`)
- Для сетевой аналитики направлений: `conntrack`

## Быстрый запуск (локально)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполни BOT_TOKEN, SUPER_OWNER_CHAT_ID, SERVER_ENDPOINT, SERVER_PUBLIC_KEY
python -m vpn_bot.main
```

## Запуск мониторинга

```bash
uvicorn monitor.app:app --host 0.0.0.0 --port 8080
```

Вход по умолчанию: `admin / admin` (смени сразу после первого входа).

## Важные переменные `.env`

- `BOT_TOKEN` - токен Telegram-бота
- `SUPER_OWNER_CHAT_ID` - chat_id владельца
- `WG_INTERFACE`, `WG_CONF` - WireGuard интерфейс/конфиг
- `SERVER_ENDPOINT`, `SERVER_PUBLIC_KEY` - параметры сервера WG
- `VPN_SUBNET`, `DNS_IP`, `KEEPALIVE` - сеть клиентов
- `CLIENTS_DIR`, `DB_PATH`, `CHAT_DIR` - пути данных
- `MONITOR_URL` - ссылка на мониторинг для кнопки в админке
- `WEB_SECRET` - секрет сессий веб-мониторинга
- `MONITOR_POLL_SEC`, `MONITOR_KEEP_DAYS`, `MONITOR_DNS_CACHE_DAYS` - параметры сбора/хранения мониторинга

## Docker

```bash
docker compose up -d --build
```

## Примечания по безопасности

- Не публикуй `.env` и приватные ключи
- Обязательно поменяй дефолтные учетные данные мониторинга
- Ограничь доступ к мониторингу по firewall/reverse proxy
