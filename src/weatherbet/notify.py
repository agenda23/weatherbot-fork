"""notify.py — Structured logging, Discord webhook, API failure tracking."""

import json
import requests
from datetime import datetime, timezone

from weatherbet.config import (
    DISCORD_WEBHOOK_URL,
    API_FAILURE_ALERT_THRESHOLD,
    LOG_FILE,
)

API_FAILURE_COUNTS: dict = {}


def log_event(level, message, **fields):
    """Console + JSON lines logger."""
    ts = datetime.now(timezone.utc).isoformat()
    level = level.upper()
    print(f"[{ts}] [{level}] {message}")
    payload = {"ts": ts, "level": level, "message": message}
    if fields:
        payload.update(fields)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def send_discord_notification(message):
    """Sends a notification to Discord webhook (if configured)."""
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        res = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=(3, 5),
        )
        return 200 <= res.status_code < 300
    except Exception:
        return False


def track_api_result(api_name, success, detail=""):
    """Tracks consecutive API failures and alerts on threshold."""
    prev = API_FAILURE_COUNTS.get(api_name, 0)
    if success:
        API_FAILURE_COUNTS[api_name] = 0
        return 0

    count = prev + 1
    API_FAILURE_COUNTS[api_name] = count
    if count == API_FAILURE_ALERT_THRESHOLD:
        msg = (
            f"API ALERT: {api_name} failed {count} times consecutively."
            + (f" Last error: {detail}" if detail else "")
        )
        log_event("ERROR", msg, api=api_name, consecutive_failures=count)
        send_discord_notification(msg)
    return count
