# 🚀 WG Hope Bot

**WG Hope Bot** — Telegram-бот для управления WireGuard-доступами + веб-мониторинг пользователей, конфигов и uplink-интерфейсов.

Проект собран для production-эксплуатации: роли, лимиты, аудит действий, регионы выхода в интернет, healthcheck каналов и автоматический failover.

## ✨ Что умеет проект

- 👥 Роли: `super_owner`, `admin`, `user`, `pending`, `banned`
- 🔐 Выдача VPN-доступа через Telegram (`.conf` + QR)
- 🧩 Лимиты, рассылки, логи, статистика, модерация заявок
- 🌍 Регионы выхода для каждого конфига:
  - `moscow` -> `eth0`
  - `latvia` -> `aw-lv` (default)
  - `amsterdam` -> `aw-am`
- 🔁 Policy routing по регионам и автоматический failover на системный uplink при падении VPN-uplink
- 🛠️ Админка серверов в боте:
  - интерфейсы/регионы
  - замена uplink-конфига
  - установка default-региона
  - удаление интерфейсов/регионов кнопками
- 📊 Веб-мониторинг:
  - пользователи/конфиги/события
  - вкладка `Серверы` (uplink-интерфейсы, uptime, скорость, ping, события состояния)
  - фильтры, графики, периоды
  - отображение `Время сервера` на всех страницах

## 🆕 Актуальный релиз

- `v1.2.3` — hardening uplink lifecycle после инцидентов с новыми регионами/интерфейсами:
  - принудительный `Table = off` при замене VPN-uplink конфига (защита от перехвата default route);
  - синхронизация `uplink_interfaces.enabled` -> `systemd` (`enable/start` или `stop/disable`) при старте бота;
  - маршрутизация учитывает только `enabled=1` интерфейсы;
  - запрет сохранения региона на выключенный/неготовый VPN-uplink;
  - улучшенная проверка uplink-статуса (`handshake_stale + probe`) в админке;
  - улучшен restart/reboot сценарий: down-alert после старта не теряется, recovery фиксируется корректно;
  - обновлены production defaults: `VPN_SUBNET=10.8.0.0/22`, запуск бота через `bot.py` в systemd unit.
- `v1.2.2` — стабильность uplink/маршрутизации + улучшенные рассылки:
  - группы получателей (`Ожидают`, `Одобренные`, `Забаненные`, `Все`) + ручное добавление `chat_id`
  - рассылка текста, фото, видео и файлов
  - подробный отчёт ошибок рассылки (`chat_id`, `@username`, причина)
  - удалённые регионы/интерфейсы больше не возвращаются после рестарта
- `v1.2.1` — UX-обновление создания конфига: короткий prompt + отдельная кнопка `Назад` для отмены
- `v1.2.0` — регионы/маршрутизация/failover, мониторинг uplink, серверная админка, вкладка `Серверы`
- `v1.1.1` — hotfix кодировки русского текста в мониторинге
- `v1.1.0` — веб-мониторинг, кастомизация, логи чатов, улучшенная админка
- `v1.0.0` — крупный рефакторинг и ролевая модель
- `v0.0.1` — первая публичная версия

## 🗂️ Структура

- `vpn_bot/` — Telegram-бот
- `monitor/` — FastAPI веб-мониторинг
- `chat/` — история чатов пользователей
- `clients/` — клиентские конфиги/QR/метаданные
- `deploy/systemd/` — шаблоны systemd-юнитов
- `deploy/scripts/` — вспомогательные скрипты развёртывания
- `deploy/SERVER_SETUP_PROD_v1.2.0.md` — пошаговая серверная настройка uplink/регионов/failover
- `CHANGELOG.md` — история изменений

## ⚙️ Требования

- Linux-сервер
- Python 3.11+
- `wg`, `wg-quick` (WireGuard)
- `iproute2`, `iptables`
- `conntrack` (для аналитики направлений трафика в мониторинге)
- Для AmneziaWG uplink-ов: `awg`, `awg-quick` и соответствующие systemd-сервисы (например `amnezia-awg@aw-lv.service`)

## ▶️ Быстрый запуск (локально)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполнить BOT_TOKEN, SUPER_OWNER_CHAT_ID, SERVER_ENDPOINT, SERVER_PUBLIC_KEY
python -m vpn_bot.main
```

## 🌐 Запуск мониторинга

```bash
uvicorn monitor.app:app --host 0.0.0.0 --port 8080
```

Вход по умолчанию: `admin / admin` (сразу сменить в `Настройки`).

## 🧪 Production-развёртывание (рекомендовано)

1. Клонировать проект в `/opt/wg-hope-bot`.
2. Создать venv и установить зависимости.
3. Заполнить `.env` (см. `.env.example`).
4. Скопировать systemd-юниты из `deploy/systemd/`.
5. Включить сервисы:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wg-hope-bot.service
sudo systemctl enable --now wg-hope-monitor.service
```

6. Проверить:

```bash
systemctl is-active wg-hope-bot.service
systemctl is-active wg-hope-monitor.service
```

## 🌍 Регионы и uplink routing

- Каждый клиентский конфиг хранит поле `region`.
- `sync_client_egress_routes()` строит policy routing и iptables для выхода через интерфейс региона.
- При падении VPN uplink трафик клиентов соответствующих регионов временно уходит через системный uplink (обычно `eth0`).
- После восстановления интерфейса маршрутизация возвращается автоматически.

## 🔑 Важные переменные `.env`

- Базовые:
  - `BOT_TOKEN`
  - `SUPER_OWNER_CHAT_ID`
  - `WG_INTERFACE`, `WG_CONF`
  - `SERVER_ENDPOINT`, `SERVER_PUBLIC_KEY`
  - `VPN_SUBNET`, `DNS_IP`, `KEEPALIVE`
- Пути:
  - `PROJECT_DIR`, `CLIENTS_DIR`, `DB_PATH`, `CHAT_DIR`
- Бот/админка:
  - `MONITOR_URL`
  - `DEFAULT_USER_LIMIT`, `ADMIN_LIMIT`
- Веб-мониторинг:
  - `WEB_SECRET`, `WEB_PORT`
  - `MONITOR_POLL_SEC`, `MONITOR_KEEP_DAYS`, `MONITOR_DNS_CACHE_DAYS`
- Healthcheck uplink:
  - `UPLINK_HEALTHCHECK_INTERVAL_SEC`
  - `UPLINK_HANDSHAKE_STALE_SEC`
  - `UPLINK_DOWN_CONFIRM_COUNT`
  - `UPLINK_UP_CONFIRM_COUNT`
  - `UPLINK_ALERT_DOWN_ON_START`

## 🐳 Docker

```bash
docker compose up -d --build
```

## 🛡️ Безопасность

- Не публиковать `.env`, приватные ключи, дампы БД и логи.
- Сменить `admin/admin` в веб-мониторинге.
- Ограничить доступ к web-морде через firewall/reverse proxy.
- Делать резервные копии `clients/` и БД перед сетевыми изменениями.
