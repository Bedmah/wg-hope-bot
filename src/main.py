import os
import json
import subprocess
from pathlib import Path
from typing import Optional
from netaddr import IPNetwork

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPER_OWNER_CHAT_ID = int(os.getenv("SUPER_OWNER_CHAT_ID", "0"))

WG_INTERFACE = os.getenv("WG_INTERFACE", "wg0")
SERVER_PUBLIC_KEY = os.getenv("SERVER_PUBLIC_KEY", "")
SERVER_ENDPOINT = os.getenv("SERVER_ENDPOINT", "")
VPN_SUBNET = os.getenv("VPN_SUBNET", "10.8.1.0/24")
DNS_IP = os.getenv("DNS_IP", "1.1.1.1")
KEEPALIVE = os.getenv("KEEPALIVE", "25")
CLIENTS_DIR = Path(os.getenv("CLIENTS_DIR", "/opt/wg-bot/clients"))
ALLOWED_IPS = os.getenv("ALLOWED_IPS", "0.0.0.0/0,::/0")
MTU = os.getenv("MTU", "1420")

ALLOC_DB = CLIENTS_DIR / "alloc.json"

def require_admin(user_id: int) -> bool:
    return user_id == SUPER_OWNER_CHAT_ID

def run(cmd: list[str], input_data: str | None = None) -> str:
    res = subprocess.run(cmd, input=input_data, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{res.stderr}")
    return res.stdout.strip()

def next_ip(subnet: str) -> str:
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    if ALLOC_DB.exists():
        data = json.loads(ALLOC_DB.read_text())
    else:
        data = {"last": 1}
    net = IPNetwork(subnet)
    idx = data.get("last", 1) + 1
    host_ip = str(net.network + idx)
    data["last"] = idx
    ALLOC_DB.write_text(json.dumps(data))
    return host_ip

def gen_keys() -> tuple[str, str]:
    priv = run(["wg", "genkey"])
    pub = run(["wg", "pubkey"], input_data=priv + "\n")
    return priv, pub

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет. Команды: /ping, /newclient <name>, /revoke <name>, /show <name>")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong")

def write_client_conf(name: str, address: str, priv: str) -> Path:
    conf = f"""[Interface]
PrivateKey = {priv}
Address = {address}
DNS = {DNS_IP}
MTU = {MTU}

[Peer]
PublicKey = {SERVER_PUBLIC_KEY}
AllowedIPs = {ALLOWED_IPS}
Endpoint = {SERVER_ENDPOINT}
PersistentKeepalive = {KEEPALIVE}
"""
    path = CLIENTS_DIR / f"{name}.conf"
    path.write_text(conf)
    return path

def add_peer_to_server(pubkey: str, address: str):
    run(["wg", "set", WG_INTERFACE, "peer", pubkey, "allowed-ips", f"{address.split('/')[0]}/32"])

def remove_peer_from_server(pubkey: str):
    run(["wg", "set", WG_INTERFACE, "peer", pubkey, "remove"])

def make_qr(path: Path) -> Path:
    png = path.with_suffix(".png")
    subprocess.check_call(["qrencode", "-o", str(png), "-t", "PNG", str(path.read_text())])
    return png

def find_pub_from_conf(path: Path) -> Optional[str]:
    priv = None
    for line in path.read_text().splitlines():
        if line.startswith("PrivateKey") and "=" in line:
            priv = line.split("=",1)[1].strip()
            break
    if not priv:
        return None
    out = run(["wg", "pubkey"], input_data=priv + "\n")
    return out.strip()

async def newclient(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not require_admin(update.effective_user.id):
        await update.message.reply_text("Нет прав")
        return
    if not context.args:
        await update.message.reply_text("Укажи имя: /newclient name")
        return
    name = context.args[0]
    address = f"{next_ip(VPN_SUBNET)}/32"
    priv, pub = gen_keys()
    conf_path = write_client_conf(name, address, priv)
    add_peer_to_server(pub, address)
    png = make_qr(conf_path)
    await update.message.reply_document(document=conf_path.open("rb"), filename=conf_path.name)
    await update.message.reply_photo(photo=png.open("rb"), caption=f"{name} {address}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not require_admin(update.effective_user.id):
        await update.message.reply_text("Нет прав")
        return
    if not context.args:
        await update.message.reply_text("Укажи имя: /revoke name")
        return
    name = context.args[0]
    conf_path = CLIENTS_DIR / f"{name}.conf"
    if not conf_path.exists():
        await update.message.reply_text("Нет такого клиента")
        return
    pub = find_pub_from_conf(conf_path)
    if pub:
        remove_peer_from_server(pub)
    conf_path.unlink(missing_ok=True)
    (conf_path.with_suffix(".png")).unlink(missing_ok=True)
    await update.message.reply_text(f"Клиент {name} удален")

async def show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи имя: /show name")
        return
    name = context.args[0]
    conf_path = CLIENTS_DIR / f"{name}.conf"
    if not conf_path.exists():
        await update.message.reply_text("Нет такого клиента")
        return
    await update.message.reply_document(document=conf_path.open("rb"), filename=conf_path.name)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN пуст")
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("newclient", newclient))
    application.add_handler(CommandHandler("revoke", revoke))
    application.add_handler(CommandHandler("show", show))
    application.run_polling(allowed_updates=None)

if __name__ == "__main__":
    main()
