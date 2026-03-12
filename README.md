# 🚀 WG Hope Bot

Привет! Это **WG Hope Bot** — наш проект для удобной выдачи WireGuard-доступов через Telegram и подробного мониторинга в веб-панели.

Мы развиваем его как практичный инструмент для реальных серверов: без лишней магии, с понятной админкой и прозрачной статистикой.

## ✨ Что умеет проект

- 👥 Роли пользователей: `super_owner`, `admin`, `user`, `pending`, `banned`
- 🔐 Выдача VPN-доступа через Telegram-бота (`.conf` + QR)
- 🧩 Лимиты, рассылки, логи, статистика, модерация заявок
- 🛠️ Синхронизация профилей Telegram (username/имя)
- 💬 История чатов пользователей: `chat/<chat_id>.log`
- 📊 Веб-мониторинг: трафик, скорость, handshake, фильтры и периоды
- 🎨 Кастомизация текстов `Инструкция` и `Вопросы / Поддержка` прямо из админки

## 🆕 Актуальный релиз

- `v1.1.0` — веб-мониторинг, кастомизация, логи чатов, улучшенная админка
- `v1.0.0` — крупный рефакторинг и ролевая модель
- `v0.0.1` — первая публичная версия

## 🗂️ Структура

- `vpn_bot/` — логика Telegram-бота
- `monitor/` — FastAPI веб-мониторинг
- `chat/` — история чатов
- `bot.py` — точка входа
- `CHANGELOG.md` — история изменений

## ⚙️ Требования

- Linux-сервер
- Python 3.11+
- WireGuard (`wg`, `wg-quick`)
- Для сетевой аналитики направлений: `conntrack`

## ▶️ Быстрый запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполни BOT_TOKEN, SUPER_OWNER_CHAT_ID, SERVER_ENDPOINT, SERVER_PUBLIC_KEY
python -m vpn_bot.main
```

## 🌐 Запуск мониторинга

```bash
uvicorn monitor.app:app --host 0.0.0.0 --port 8080
```

Вход по умолчанию: `admin / admin` (рекомендуем сменить сразу после первого входа).

## 🔑 Важные переменные `.env`

- `BOT_TOKEN` — токен Telegram-бота
- `SUPER_OWNER_CHAT_ID` — chat_id владельца
- `WG_INTERFACE`, `WG_CONF` — параметры WireGuard
- `SERVER_ENDPOINT`, `SERVER_PUBLIC_KEY` — данные сервера WG
- `VPN_SUBNET`, `DNS_IP`, `KEEPALIVE` — сеть клиентов
- `CLIENTS_DIR`, `DB_PATH`, `CHAT_DIR` — пути хранения данных
- `MONITOR_URL` — ссылка на мониторинг для кнопки в админке
- `WEB_SECRET` — секрет сессий веб-мониторинга
- `MONITOR_POLL_SEC`, `MONITOR_KEEP_DAYS`, `MONITOR_DNS_CACHE_DAYS` — параметры сбора и хранения

## 🐳 Docker

```bash
docker compose up -d --build
```

## 🛡️ Безопасность

- Не публикуйте `.env`, приватные ключи и дампы БД
- Обязательно смените дефолтные учетные данные веб-мониторинга
- Ограничьте доступ к мониторингу через firewall / reverse proxy

---

Спасибо всем, кто пользуется проектом и дает обратную связь ❤️

**Команда WG Hope Bot**
