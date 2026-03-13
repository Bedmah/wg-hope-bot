from __future__ import annotations

from telegram.ext import Application

from . import db
from .handlers import register_handlers
from .monitoring import HEALTHCHECK_INTERVAL_SEC, run_uplink_healthcheck
from .routing import sync_client_egress_routes
from .server_admin import sync_interface_services
from .settings import BOT_TOKEN, SERVER_PUBLIC_KEY, SERVER_ENDPOINT


def run() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN")
    if not SERVER_PUBLIC_KEY:
        raise SystemExit("Set SERVER_PUBLIC_KEY")
    if not SERVER_ENDPOINT:
        raise SystemExit("Set SERVER_ENDPOINT")

    db.init()
    try:
        sync_interface_services()
    except Exception:
        pass
    try:
        sync_client_egress_routes()
    except Exception:
        # Bot should still start even if route sync temporarily fails.
        pass

    app = Application.builder().token(BOT_TOKEN).build()
    register_handlers(app)
    if app.job_queue:
        app.job_queue.run_repeating(run_uplink_healthcheck, interval=HEALTHCHECK_INTERVAL_SEC, first=20)
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    run()
