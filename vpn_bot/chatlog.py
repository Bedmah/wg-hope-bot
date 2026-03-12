from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .settings import CHAT_DIR


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_for(chat_id: str) -> Path:
    return CHAT_DIR / f"{chat_id}.log"


def append(chat_id: str, direction: str, text: str) -> None:
    if not chat_id:
        return
    line = f"[{_ts()}] {direction}: {text}\n"
    path = _file_for(str(chat_id))
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
