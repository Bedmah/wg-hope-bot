# Server Setup (Production) v1.2.0

Этот документ про **серверную часть**: как подготовить хост, чтобы бот управлял регионами/интерфейсами и пускал трафик пользователей через выбранные uplink-интерфейсы.

## 1) Базовая подготовка сервера

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl \
  wireguard-tools iproute2 iptables conntrack
```

Для AmneziaWG uplink-ов на сервере должны быть установлены `awg`/`awg-quick` и systemd-сервисы вида `amnezia-awg@<ifname>.service`.

## 2) Развёртывание проекта

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

Заполнить `.env` (без секретов в git):
- `BOT_TOKEN`
- `SUPER_OWNER_CHAT_ID`
- `SERVER_ENDPOINT`
- `SERVER_PUBLIC_KEY`
- `WG_INTERFACE` (обычно `wg0`)
- `WG_CONF` (обычно `/etc/wireguard/wg0.conf`)
- `WEB_SECRET`
- `MONITOR_URL`

## 3) Включить IP forwarding

```bash
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-wg-hope.conf
sudo sysctl --system
```

## 4) Поднять uplink-интерфейсы (пример AmneziaWG)

Пример: `aw-lv` и `aw-am`.

1. Положить конфиги:
```bash
sudo mkdir -p /etc/amnezia/amneziawg
sudo nano /etc/amnezia/amneziawg/aw-lv.conf
sudo nano /etc/amnezia/amneziawg/aw-am.conf
sudo chmod 600 /etc/amnezia/amneziawg/aw-lv.conf /etc/amnezia/amneziawg/aw-am.conf
```

2. Включить сервисы:
```bash
sudo systemctl enable --now amnezia-awg@aw-lv.service
sudo systemctl enable --now amnezia-awg@aw-am.service
sudo systemctl is-active amnezia-awg@aw-lv.service
sudo systemctl is-active amnezia-awg@aw-am.service
```

## 5) Подключить сервисы проекта

```bash
sudo cp deploy/systemd/wg-hope-bot.service /etc/systemd/system/
sudo cp deploy/systemd/wg-hope-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wg-hope-bot.service
sudo systemctl enable --now wg-hope-monitor.service
```

Проверка:
```bash
systemctl is-active wg-hope-bot.service
systemctl is-active wg-hope-monitor.service
```

## 6) Первичная настройка через бота (Админка -> Сервера)

Настраивается именно через бот, без ручного редактирования БД:

1. `Интерфейсы` — проверить, что есть:
- `eth0` (`system`)
- `aw-lv` (`amneziawg`)
- `aw-am` (`amneziawg`)

2. Если нужно добавить интерфейс:
- `Добавить интерфейс`
- формат: `<ifname> <kind> [table_id]`
- пример: `aw-de amneziawg 210`

3. Регионы:
- `Добавить/изменить регион`
- формат: `<code>;<label>;<iface>[;default]`
- пример: `latvia;Латвия;aw-lv;default`
- пример: `moscow;Москва;eth0`
- пример: `amsterdam;Амстердам;aw-am`

4. Удаление:
- `Удалить интерфейс` — выбрать кнопкой
- `Удалить регион` — выбрать кнопкой

5. Проверка:
- `Проверить сервера`

## 7) Как серверная маршрутизация работает

После любых изменений интерфейсов/регионов/конфигов вызывается `sync_client_egress_routes()`:

- создаются/обновляются записи в `/etc/iproute2/rt_tables`;
- настраиваются route table для uplink-интерфейсов;
- для каждого клиента создаётся `ip rule from <client_ip>/32 lookup <table>`;
- добавляются `iptables` правила `MASQUERADE` и `FORWARD`.

Это делает бот автоматически, вручную правила создавать не нужно.

## 8) Failover (если uplink упал)

- healthcheck запускается периодически (`UPLINK_HEALTHCHECK_INTERVAL_SEC`);
- если uplink недоступен, регион временно переводится на системный uplink (обычно `eth0`);
- когда uplink восстановился, регион автоматически возвращается обратно.

Параметры в `.env`:
- `UPLINK_HEALTHCHECK_INTERVAL_SEC`
- `UPLINK_HANDSHAKE_STALE_SEC`
- `UPLINK_DOWN_CONFIRM_COUNT`
- `UPLINK_UP_CONFIRM_COUNT`

## 9) Проверка серверной части

```bash
# статусы сервисов
systemctl status wg-hope-bot.service --no-pager
systemctl status wg-hope-monitor.service --no-pager

# uplink сервисы
systemctl status amnezia-awg@aw-lv.service --no-pager
systemctl status amnezia-awg@aw-am.service --no-pager

# интерфейсы
ip link show aw-lv
ip link show aw-am
ip link show eth0

# правила policy routing
ip -4 rule show
ip route show table all | head -n 200

# nat/forward правила
iptables -t nat -S | grep MASQUERADE
iptables -S FORWARD
```

## 10) Что не хранить в репозитории

- `.env`
- любые приватные ключи/конфиги с ключами
- логи
- БД/дампы БД
