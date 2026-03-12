from __future__ import annotations

from telegram.ext import Application

from . import db
from .handlers import register_handlers
from .settings import BOT_TOKEN, SERVER_PUBLIC_KEY, SERVER_ENDPOINT


def run() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN")
    if not SERVER_PUBLIC_KEY:
        raise SystemExit("Set SERVER_PUBLIC_KEY")
    if not SERVER_ENDPOINT:
        raise SystemExit("Set SERVER_ENDPOINT")

    db.init()

    app = Application.builder().token(BOT_TOKEN).build()
    register_handlers(app)
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    run()
