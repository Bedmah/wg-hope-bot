# WG Hope Bot v1.2.0

Релиз `v1.2.0` — это production-обновление с полноценной региональной маршрутизацией, uplink healthcheck/failover, серверной админкой и расширенным веб-мониторингом.

## 🚀 Что нового

- 🌍 Регион у каждого клиентского конфига и выбор региона пользователем в боте.
- 🔁 Policy routing по регионам через отдельные uplink-интерфейсы.
- 🧯 Авто-failover на системный uplink (например `eth0`) при падении VPN uplink.
- 🛠️ Админка `Сервера` в боте:
  - интерфейсы/регионы;
  - default-регион;
  - замена uplink-конфигов;
  - удаление интерфейсов/регионов кнопками.
- 📊 Новая вкладка `Серверы` в веб-мониторинге:
  - статистика регионов;
  - состояние uplink-интерфейсов, uptime, скорость, ping;
  - графики и история `DOWN/RECOVERY`.
- 🕒 `Время сервера` добавлено на все страницы веб-мониторинга.
- 🧾 В событиях пользователя добавлена смена региона (`region_change`) + фильтр по этому типу.

## 🧱 Production схема (как в проде)

- Вход VPN клиентов: `wg0` (`WG_INTERFACE`)
- Uplink-интерфейсы:
  - `eth0` (`system`) -> регион `moscow`
  - `aw-lv` (`amneziawg`) -> регион `latvia` (default)
  - `aw-am` (`amneziawg`) -> регион `amsterdam`
- Маршрутизация:
  - policy rules (`ip rule from <client_ip>/32 lookup <table>`)
  - `rt_tables` для uplink-таблиц
  - `iptables` (`MASQUERADE` + `FORWARD`)
- Автовосстановление:
  - ботовый healthcheck uplink-ов;
  - при `down` интерфейса клиенты региона временно уходят на системный uplink;
  - при `recovery` возвращаются.

## ⚙️ Пошаговый деплой v1.2.0 (без секретов)

1. Подготовка сервера:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip wireguard-tools iproute2 iptables conntrack curl
```

2. Развёртывание проекта:

```bash
sudo mkdir -p /opt/wg-hope-bot
sudo chown -R $USER:$USER /opt/wg-hope-bot
cd /opt/wg-hope-bot
git clone https://github.com/Bedmah/wg-hope-bot .
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

3. Заполнить `.env` (только своими данными):
- `BOT_TOKEN`
- `SUPER_OWNER_CHAT_ID`
- `SERVER_ENDPOINT`
- `SERVER_PUBLIC_KEY`
- `WEB_SECRET`
- `MONITOR_URL`
- `UPLINK_*` и `MONITOR_*` при необходимости

4. Подключить systemd:

```bash
sudo cp deploy/systemd/wg-hope-bot.service /etc/systemd/system/
sudo cp deploy/systemd/wg-hope-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wg-hope-bot.service
sudo systemctl enable --now wg-hope-monitor.service
```

5. Проверка:

```bash
systemctl is-active wg-hope-bot.service
systemctl is-active wg-hope-monitor.service
journalctl -u wg-hope-bot.service -n 50 --no-pager
journalctl -u wg-hope-monitor.service -n 50 --no-pager
```

## 🧪 Базовая пост-проверка

- В боте:
  - `Админка -> Сервера -> Интерфейсы`
  - `Админка -> Сервера -> Регионы`
  - `Админка -> Сервера -> Проверить сервера`
- В вебе:
  - `/` — пользователи
  - `/servers` — uplink/регионы/графики
  - у каждого конфига виден текущий регион
  - в событиях видна смена региона

## 📁 Что добавлено/обновлено в репозитории

- Документация:
  - `README.md`
  - `CHANGELOG.md`
  - `.env.example`
  - `RELEASE_NOTES_v1.2.0.md`
- Deploy-шаблоны:
  - `deploy/systemd/wg-hope-bot.service`
  - `deploy/systemd/wg-hope-monitor.service`
  - `deploy/scripts/install_prod_v1_2_0.sh`

## ⚠️ Важно

- В релиз и документацию не включены:
  - секреты (`.env`, ключи);
  - логи;
  - БД.
- После первого входа в веб обязательно сменить `admin/admin`.
