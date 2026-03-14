from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vpn_bot import db
from vpn_bot.routing import (
    PRIO_BASE,
    PRIO_MAX,
    sync_client_egress_routes,
    table_name_for_iface,
)
from vpn_bot.server_admin import interface_status
from vpn_bot.settings import BOT_TOKEN, DB_PATH, WG_INTERFACE


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True)


def _run_ok(*args: str) -> bool:
    return _run(*args).returncode == 0


def _now_local() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _human_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    num = float(max(0, value))
    for unit in units:
        if num < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.2f} {unit}"
        num /= 1024
    return f"{int(value)} B"


@dataclass
class CheckResult:
    name: str
    status: str
    details: str


def _cleanup_orphan_health_rows() -> CheckResult:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(
                """
                DELETE FROM uplink_health
                WHERE interface_name NOT IN (SELECT name FROM uplink_interfaces)
                """
            )
            deleted = int(cur.rowcount or 0)
            conn.commit()
        if deleted > 0:
            return CheckResult("orphan_health_cleanup", "FIXED", f"Удалено устаревших записей: {deleted}")
        return CheckResult("orphan_health_cleanup", "OK", "Устаревших записей не найдено")
    except Exception as exc:
        return CheckResult("orphan_health_cleanup", "WARN", f"Не удалось очистить записи: {exc}")


def _guess_system_default_route() -> tuple[str, str] | None:
    rows = db.list_uplink_interfaces()
    system_ifaces = [r["name"] for r in rows if int(r["enabled"]) == 1 and r["kind"] == "system"]
    if not system_ifaces:
        system_ifaces = ["eth0"]
    for iface in system_ifaces:
        p = _run("ip", "-4", "route", "show", "dev", iface)
        if p.returncode != 0:
            continue
        for line in p.stdout.splitlines():
            m = re.search(r"^\s*([0-9.]+)\s+dev\s+" + re.escape(iface) + r"\s+proto\s+dhcp\s+scope\s+link", line.strip())
            if m:
                return m.group(1), iface
    return None


def _check_default_route() -> CheckResult:
    p = _run("ip", "-4", "route", "show", "table", "main")
    if p.returncode != 0:
        return CheckResult("default_route", "FAIL", "Не удалось прочитать таблицу маршрутов main")
    default_line = next((x.strip() for x in p.stdout.splitlines() if x.strip().startswith("default ")), "")
    if default_line:
        via_ok = " via " in default_line
        dev_m = re.search(r"\bdev\s+(\S+)", default_line)
        dev = dev_m.group(1) if dev_m else "?"
        rows = db.list_uplink_interfaces()
        system_ifaces = {r["name"] for r in rows if int(r["enabled"]) == 1 and r["kind"] == "system"}
        if system_ifaces and dev not in system_ifaces:
            fix = _guess_system_default_route()
            if fix:
                gw, iface = fix
                rep = _run("ip", "route", "replace", "default", "via", gw, "dev", iface, "metric", "100")
                if rep.returncode == 0:
                    return CheckResult("default_route", "FIXED", f"Маршрут исправлен: {default_line} -> via {gw} dev {iface}")
            return CheckResult("default_route", "FAIL", f"Default route указывает на несистемный интерфейс: {default_line}")
        if not via_ok:
            return CheckResult("default_route", "WARN", f"Нестандартный формат default route: {default_line}")
        return CheckResult("default_route", "OK", default_line)
    fix = _guess_system_default_route()
    if not fix:
        return CheckResult("default_route", "FAIL", "Default route отсутствует, fallback-шлюз не найден")
    gw, iface = fix
    rep = _run("ip", "route", "replace", "default", "via", gw, "dev", iface, "metric", "100")
    if rep.returncode == 0:
        return CheckResult("default_route", "FIXED", f"Default route восстановлен: via {gw} dev {iface}")
    return CheckResult("default_route", "FAIL", f"Не удалось восстановить маршрут via {gw} dev {iface}: {rep.stderr.strip()}")


def _check_ip_rules_and_cleanup() -> CheckResult:
    rows = db.list_uplink_interfaces()
    expected_table_names = set()
    expected_table_ids = set()
    for r in rows:
        if int(r["enabled"]) != 1:
            continue
        tid = r["table_id"]
        if tid is None:
            continue
        expected_table_ids.add(str(int(tid)))
        expected_table_names.add(table_name_for_iface(r["name"]))

    p = _run("ip", "-4", "rule", "show")
    if p.returncode != 0:
        return CheckResult("ip_rules", "FAIL", "Не удалось прочитать ip rule")

    deleted = 0
    bad: list[str] = []
    managed_count = 0
    for line in p.stdout.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        pref_s, rest = line.split(":", 1)
        pref_s = pref_s.strip()
        if not pref_s.isdigit():
            continue
        pref = int(pref_s)
        if pref < PRIO_BASE or pref >= PRIO_MAX:
            continue
        managed_count += 1
        m = re.search(r"\blookup\s+(\S+)", rest)
        lookup = m.group(1) if m else ""
        is_ok = lookup in expected_table_names or lookup in expected_table_ids
        if not is_ok:
            bad.append(line)
            d = _run("ip", "-4", "rule", "del", "priority", str(pref))
            if d.returncode == 0:
                deleted += 1

    if bad and deleted > 0:
        return CheckResult("ip_rules", "FIXED", f"Проверено managed-правил: {managed_count}, удалено устаревших: {deleted}")
    if bad:
        return CheckResult("ip_rules", "FAIL", f"Остались устаревшие правила: {len(bad)}")
    return CheckResult("ip_rules", "OK", f"Проверено managed-правил: {managed_count}, устаревших нет")


def _check_wg0() -> CheckResult:
    p = _run("ip", "link", "show", "dev", WG_INTERFACE)
    if p.returncode != 0:
        return CheckResult("wg_interface", "FAIL", f"{WG_INTERFACE}: интерфейс не найден")
    up = "state UP" in p.stdout or "LOWER_UP" in p.stdout
    return CheckResult("wg_interface", "OK" if up else "WARN", f"{WG_INTERFACE}: {'UP' if up else 'не UP'}")


def _service_active(name: str) -> tuple[bool, str]:
    p = _run("systemctl", "is-active", name)
    state = (p.stdout or "").strip() or (p.stderr or "").strip() or "unknown"
    return state == "active", state


def _service_state_ru(state: str) -> str:
    m = {
        "active": "активен",
        "inactive": "неактивен",
        "failed": "ошибка",
        "activating": "запускается",
        "deactivating": "останавливается",
        "unknown": "неизвестно",
    }
    return m.get((state or "").strip().lower(), state)


def _check_core_services() -> list[CheckResult]:
    out: list[CheckResult] = []
    for svc in [f"wg-quick@{WG_INTERFACE}", "wg-hope-bot", "wg-hope-monitor"]:
        ok, state = _service_active(svc)
        out.append(CheckResult(f"service:{svc}", "OK" if ok else "FAIL", _service_state_ru(state)))
    ssh_name = "ssh" if _run("systemctl", "status", "ssh").returncode == 0 else "sshd"
    ok, state = _service_active(ssh_name)
    out.append(CheckResult(f"service:{ssh_name}", "OK" if ok else "FAIL", _service_state_ru(state)))
    p = _run("ss", "-ltnp")
    listens_22 = p.returncode == 0 and ":22" in p.stdout
    out.append(CheckResult("ssh_listen_22", "OK" if listens_22 else "FAIL", "Порт 22 прослушивается" if listens_22 else "Порт 22 не прослушивается"))
    return out


def _ensure_table_off_config(iface_row: sqlite3.Row) -> CheckResult | None:
    if iface_row["kind"] not in ("amneziawg", "wireguard"):
        return None
    cfg = (iface_row["config_path"] or "").strip()
    if not cfg:
        return CheckResult(f"table_off:{iface_row['name']}", "WARN", "Путь к конфигу не указан")
    path = Path(cfg)
    if not path.exists():
        return CheckResult(f"table_off:{iface_row['name']}", "WARN", f"Конфиг не найден: {cfg}")
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    out: list[str] = []
    in_interface = False
    saw_table = False
    changed = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_interface and not saw_table:
                out.append("Table = off")
                changed = True
            in_interface = stripped.lower() == "[interface]"
            saw_table = False
            out.append(line)
            continue
        if in_interface and re.match(r"(?i)^table\s*=", stripped):
            if stripped.lower() != "table = off":
                changed = True
            if not saw_table:
                out.append("Table = off")
                saw_table = True
            continue
        out.append(line)
    if in_interface and not saw_table:
        out.append("Table = off")
        changed = True
    if not changed:
        return CheckResult(f"table_off:{iface_row['name']}", "OK", "Параметр Table = off уже установлен")
    path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass
    svc = (iface_row["service_name"] or "").strip()
    if svc:
        _run("systemctl", "restart", svc)
    return CheckResult(f"table_off:{iface_row['name']}", "FIXED", "Конфиг исправлен: установлен Table = off")


def _check_interfaces_and_regions() -> list[CheckResult]:
    out: list[CheckResult] = []
    interfaces = db.list_uplink_interfaces()
    iface_by_name = {r["name"]: r for r in interfaces}

    for iface in interfaces:
        if int(iface["enabled"]) != 1:
            continue
        r = _ensure_table_off_config(iface)
        if r:
            out.append(r)
        if iface["kind"] == "system":
            p = _run("ip", "link", "show", "dev", iface["name"])
            out.append(CheckResult(f"iface:{iface['name']}", "OK" if p.returncode == 0 else "FAIL", "Интерфейс присутствует" if p.returncode == 0 else "Интерфейс отсутствует"))
            continue
        svc = (iface["service_name"] or "").strip()
        if svc:
            ok, state = _service_active(svc)
            out.append(CheckResult(f"service:{svc}", "OK" if ok else "FAIL", _service_state_ru(state)))
        ok, details = interface_status(iface["name"])
        details_ru = (
            details.replace("service=active", "сервис=активен")
            .replace("service=inactive", "сервис=неактивен")
            .replace("handshake=none", "handshake=нет")
            .replace("handshake_stale>", "устаревший_handshake>")
            .replace("probe=ok", "проверка_связи=ок")
            .replace("probe=fail", "проверка_связи=ошибка")
            .replace("link not found", "интерфейс не найден")
        )
        out.append(CheckResult(f"iface_health:{iface['name']}", "OK" if ok else "FAIL", details_ru))

    regions = db.list_regions()
    for region in regions:
        iface = iface_by_name.get(region["interface_name"])
        if not iface:
            out.append(CheckResult(f"region:{region['code']}", "FAIL", f"Интерфейс {region['interface_name']} не найден"))
            continue
        if int(iface["enabled"]) != 1:
            out.append(CheckResult(f"region:{region['code']}", "FAIL", f"Интерфейс {region['interface_name']} выключен"))
            continue
        out.append(CheckResult(f"region:{region['code']}", "OK", f"{region['label']} -> {region['interface_name']}"))

    region_codes = {r["code"] for r in regions}
    unknown_client_regions = sorted({(c["region"] or "").strip() for c in db.list_all_clients() if (c["region"] or "").strip() and (c["region"] or "").strip() not in region_codes})
    if unknown_client_regions:
        out.append(CheckResult("clients_region_refs", "WARN", f"У клиентов есть неизвестные регионы: {','.join(unknown_client_regions[:20])}"))
    else:
        out.append(CheckResult("clients_region_refs", "OK", "У всех клиентов указаны валидные регионы"))
    return out


def _check_resources() -> CheckResult:
    disk = shutil.disk_usage("/")
    disk_pct = (float(disk.used) / float(disk.total) * 100.0) if disk.total else 0.0
    mem_total = 0
    mem_avail = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1])
            if line.startswith("MemAvailable:"):
                mem_avail = int(line.split()[1])
    except Exception:
        pass
    mem_pct = (float(max(mem_total - mem_avail, 0)) / float(mem_total) * 100.0) if mem_total else 0.0
    try:
        la1, la5, la15 = os.getloadavg()
    except Exception:
        la1, la5, la15 = 0.0, 0.0, 0.0
    detail = (
        f"Load average: {la1:.2f}/{la5:.2f}/{la15:.2f}, "
        f"RAM: {mem_pct:.1f}% ({_human_bytes(mem_total * 1024 - mem_avail * 1024)}/{_human_bytes(mem_total * 1024)}), "
        f"Disk: {disk_pct:.1f}% ({_human_bytes(disk.used)}/{_human_bytes(disk.total)})"
    )
    status = "OK"
    if disk_pct >= 95 or mem_pct >= 95:
        status = "FAIL"
    elif disk_pct >= 85 or mem_pct >= 90:
        status = "WARN"
    return CheckResult("resources", status, detail)


def _format_report(results: list[CheckResult], started_at: float) -> str:
    counts = {"OK": 0, "WARN": 0, "FAIL": 0, "FIXED": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    elapsed = time.time() - started_at
    status_ru = {"OK": "ОК", "FIXED": "ИСПРАВЛЕНО", "WARN": "ПРЕДУПРЕЖДЕНИЕ", "FAIL": "ОШИБКА"}
    name_ru = {
        "orphan_health_cleanup": "Очистка устаревших записей health",
        "default_route": "Проверка default route",
        "ip_rules": "Проверка ip rule",
        "wg_interface": "Проверка интерфейса WireGuard",
        "ssh_listen_22": "Проверка SSH порта 22",
        "clients_region_refs": "Проверка регионов у клиентов",
        "sync_routes": "Синхронизация маршрутизации клиентов",
        "resources": "Проверка ресурсов сервера",
    }
    lines = [
        "[АВТОПРОВЕРКА ПОСЛЕ ПЕРЕЗАГРУЗКИ]",
        f"Время: {_now_local()}",
        f"Итог: ОК={counts['OK']} | ИСПРАВЛЕНО={counts['FIXED']} | ПРЕДУПРЕЖДЕНИЙ={counts['WARN']} | ОШИБОК={counts['FAIL']}",
        f"Длительность: {elapsed:.1f} сек",
        "",
    ]
    for r in results:
        label = r.name
        if r.name.startswith("service:"):
            label = "Сервис " + r.name.split(":", 1)[1]
        elif r.name.startswith("table_off:"):
            label = "Проверка Table=off для " + r.name.split(":", 1)[1]
        elif r.name.startswith("iface_health:"):
            label = "Состояние интерфейса " + r.name.split(":", 1)[1]
        elif r.name.startswith("iface:"):
            label = "Проверка интерфейса " + r.name.split(":", 1)[1]
        elif r.name.startswith("region:"):
            label = "Проверка региона " + r.name.split(":", 1)[1]
        else:
            label = name_ru.get(r.name, r.name)
        lines.append(f"[{status_ru.get(r.status, r.status)}] {label}: {r.details}")
    return "\n".join(lines)


def _send_telegram(chat_id: str, text: str) -> tuple[bool, str]:
    if not BOT_TOKEN:
        return False, "BOT_TOKEN пустой"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        parsed = json.loads(body)
        if not parsed.get("ok"):
            return False, parsed.get("description", "ошибка Telegram API")
        return True, "ок"
    except urllib.error.HTTPError as exc:
        return False, f"http {exc.code}"
    except Exception as exc:
        return False, str(exc)


def _split_chunks(text: str, chunk_size: int = 3500) -> list[str]:
    out: list[str] = []
    s = text
    while len(s) > chunk_size:
        idx = s.rfind("\n", 0, chunk_size)
        if idx <= 0:
            idx = chunk_size
        out.append(s[:idx])
        s = s[idx:].lstrip("\n")
    if s:
        out.append(s)
    return out


def main() -> int:
    started = time.time()
    results: list[CheckResult] = []
    results.append(_cleanup_orphan_health_rows())
    results.append(_check_default_route())
    results.append(_check_ip_rules_and_cleanup())
    results.append(_check_wg0())
    results.extend(_check_core_services())
    results.extend(_check_interfaces_and_regions())
    try:
        sync_client_egress_routes()
        results.append(CheckResult("sync_routes", "OK", "Маршруты и правила применены"))
    except Exception as exc:
        results.append(CheckResult("sync_routes", "FAIL", f"Ошибка применения маршрутов: {exc}"))
    results.append(_check_ip_rules_and_cleanup())
    results.append(_check_resources())

    report = _format_report(results, started)
    print(report)

    admins = db.admin_chat_ids()
    send_errors: list[str] = []
    for admin_id in admins:
        for part in _split_chunks(report):
            ok, detail = _send_telegram(admin_id, part)
            if not ok:
                send_errors.append(f"{admin_id}:{detail}")
                break
    if send_errors:
        print("ошибки_отправки_в_telegram:", ", ".join(send_errors))

    fail_count = sum(1 for r in results if r.status == "FAIL")
    return 2 if fail_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
