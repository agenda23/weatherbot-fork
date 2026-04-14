"""risk.py — Position risk checks and dynamic thresholds."""

from datetime import datetime, timezone


def get_today_realized_loss(markets, now=None):
    """Returns today's realized loss amount (positive number)."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone.utc).date()
    realized_loss = 0.0

    for mkt in markets:
        pos = mkt.get("position") or {}
        pnl = pos.get("pnl")
        closed_at = pos.get("closed_at")
        if pnl is None or pnl >= 0 or not closed_at:
            continue
        try:
            closed_dt = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        except Exception:
            continue
        if closed_dt.astimezone(timezone.utc).date() == today:
            realized_loss += -float(pnl)

    return round(realized_loss, 2)


def has_open_position_for_city_date(markets, city_slug, date_str):
    """Returns True if an open position already exists for the same city/date."""
    for mkt in markets:
        if mkt.get("city") != city_slug or mkt.get("date") != date_str:
            continue
        pos = mkt.get("position") or {}
        if pos.get("status") == "open":
            return True
    return False


def calc_take_profit_threshold(hours_left):
    """Continuous take-profit threshold by time-to-resolution.

    - <24h:   None (hold to resolution)
    - 24-48h: linearly from 0.85 -> 0.75
    - >=48h:  0.75
    """
    if hours_left < 24:
        return None
    if hours_left >= 48:
        return 0.75
    ratio = (hours_left - 24.0) / 24.0
    threshold = 0.85 - (0.10 * ratio)
    return round(threshold, 3)


def calc_dynamic_stop_price(entry_price, sigma, unit):
    """Sigma-aware dynamic stop loss price.

    Base loss is 20%, then scaled by sigma / baseline_sigma.
    Wider sigma -> wider stop (lower stop price).
    """
    baseline_sigma = 2.0 if unit == "F" else 1.2
    s = float(sigma) if sigma is not None else baseline_sigma
    scale = s / baseline_sigma if baseline_sigma > 0 else 1.0
    loss_pct = 0.20 * scale
    loss_pct = min(max(loss_pct, 0.10), 0.35)
    return round(entry_price * (1.0 - loss_pct), 4)
