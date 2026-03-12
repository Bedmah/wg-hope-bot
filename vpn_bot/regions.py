from __future__ import annotations

import os
import re

REGION_MOSCOW = "moscow"
REGION_LATVIA = "latvia"
REGION_AMSTERDAM = "amsterdam"

REGION_DEFAULT = os.environ.get("REGION_DEFAULT", REGION_LATVIA).strip().lower() or REGION_LATVIA
DEFAULT_REGION_LABELS = {
    REGION_MOSCOW: "Moscow",
    REGION_LATVIA: "Latvia",
    REGION_AMSTERDAM: "Amsterdam",
}


def normalize_region(value: str | None) -> str:
    raw = (value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-")
    return slug or REGION_DEFAULT


def region_label(value: str | None) -> str:
    code = normalize_region(value)
    return DEFAULT_REGION_LABELS.get(code, code)
