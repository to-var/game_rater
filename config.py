"""
Shared persistent config for game_rater: TheGamesDB API key and monthly API
call usage tracking. Used by both scrape_metadata.py (CLI) and app.py (GUI)
so the key only needs to be entered once and usage is tracked regardless of
entry point.

Stored at ~/.game_rater/config.json:
    {
        "tgdb_api_key": "...",
        "usage": {"month": "2026-08", "count": 17}
    }
"""

import json
from datetime import date
from pathlib import Path

CONFIG_DIR = Path.home() / ".game_rater"
CONFIG_PATH = CONFIG_DIR / "config.json"

MONTHLY_ALLOWANCE = 1000


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get_api_key() -> str | None:
    return load_config().get("tgdb_api_key")


def set_api_key(api_key: str) -> None:
    cfg = load_config()
    cfg["tgdb_api_key"] = api_key
    save_config(cfg)


def get_usage() -> tuple[int, int]:
    """Returns (calls_made_this_month, monthly_allowance), resetting the
    counter if the month has rolled over since the last recorded call."""
    cfg = load_config()
    usage = cfg.get("usage", {})
    if usage.get("month") != _current_month():
        return 0, MONTHLY_ALLOWANCE
    return usage.get("count", 0), MONTHLY_ALLOWANCE


def record_api_call() -> int:
    """Increments and persists the usage counter, resetting it if the month
    has rolled over. Returns the new call count."""
    cfg = load_config()
    usage = cfg.get("usage", {})
    month = _current_month()
    if usage.get("month") != month:
        usage = {"month": month, "count": 0}
    usage["count"] = usage.get("count", 0) + 1
    cfg["usage"] = usage
    save_config(cfg)
    return usage["count"]
