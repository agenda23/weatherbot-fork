"""polymarket.py — Polymarket Gamma API read-only access."""

import json
import requests

from weatherbet.notify import track_api_result, log_event


def get_polymarket_event(city_slug, month, day, year):
    slug = f"highest-temperature-in-{city_slug}-on-{month}-{day}-{year}"
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=(5, 8))
        data = r.json()
        track_api_result("polymarket_event", True)
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
    except Exception as e:
        track_api_result("polymarket_event", False, str(e))
        log_event("WARNING", f"[POLY_EVENT] {city_slug}: {e}", city=city_slug)
    return None


def get_market_price(market_id):
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=(3, 5))
        prices = json.loads(r.json().get("outcomePrices", "[0.5,0.5]"))
        track_api_result("polymarket_market", True)
        return float(prices[0])
    except Exception as e:
        track_api_result("polymarket_market", False, str(e))
        return None


def check_market_resolved(market_id):
    """Returns None (open), True (YES won), or False (NO won)."""
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=(5, 8))
        data = r.json()
        closed = data.get("closed", False)
        if not closed:
            return None
        prices = json.loads(data.get("outcomePrices", "[0.5,0.5]"))
        yes_price = float(prices[0])
        track_api_result("polymarket_market", True)
        if yes_price >= 0.95:
            return True
        elif yes_price <= 0.05:
            return False
        return None
    except Exception as e:
        track_api_result("polymarket_market", False, str(e))
        log_event("WARNING", f"[RESOLVE] {market_id}: {e}", market_id=market_id)
    return None
