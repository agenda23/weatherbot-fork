"""state.py — Bot balance and trade-count state persistence."""

import json

from weatherbet.config import STATE_FILE, BALANCE


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "balance":          BALANCE,
        "starting_balance": BALANCE,
        "total_trades":     0,
        "wins":             0,
        "losses":           0,
        "peak_balance":     BALANCE,
    }


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
