#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_v2.py — Weather Trading Bot for Polymarket
=====================================================
Tracks weather forecasts from 3 sources (ECMWF, HRRR, METAR),
compares with Polymarket markets, paper trades using Kelly criterion.

Usage:
    python bot_v2.py          # main loop
    python bot_v2.py report   # full report
    python bot_v2.py status   # balance and open positions
"""

import re
import sys
import json
import math
import time
import hashlib
import webbrowser
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# =============================================================================
# CONFIG
# =============================================================================

with open("config.json", encoding="utf-8") as f:
    _cfg = json.load(f)

BALANCE          = _cfg.get("balance", 10000.0)
MAX_BET          = _cfg.get("max_bet", 20.0)        # max bet per trade
MIN_EV           = _cfg.get("min_ev", 0.10)
MAX_PRICE        = _cfg.get("max_price", 0.45)
MIN_VOLUME       = _cfg.get("min_volume", 500)
MIN_HOURS        = _cfg.get("min_hours", 2.0)
MAX_HOURS        = _cfg.get("max_hours", 72.0)
KELLY_FRACTION   = _cfg.get("kelly_fraction", 0.25)
MAX_SLIPPAGE     = _cfg.get("max_slippage", 0.03)  # max allowed ask-bid spread
SCAN_INTERVAL    = _cfg.get("scan_interval", 3600)   # every hour
CALIBRATION_MIN  = _cfg.get("calibration_min", 30)
DAILY_LOSS_LIMIT_PCT = max(0.0, float(_cfg.get("daily_loss_limit_pct", 0.10)))
DISCORD_WEBHOOK_URL = _cfg.get("discord_webhook_url", "").strip()
API_FAILURE_ALERT_THRESHOLD = max(1, int(_cfg.get("api_failure_alert_threshold", 3)))
VC_KEY           = _cfg.get("vc_key", "")
CLOB_BASE_URL    = _cfg.get("clob_base_url", "https://clob.polymarket.com").rstrip("/")
CLOB_API_KEY     = _cfg.get("clob_api_key", "").strip()
POLYGON_WALLET_ADDRESS = _cfg.get("polygon_wallet_address", "").strip()
POLYGON_PRIVATE_KEY = _cfg.get("polygon_private_key", "").strip()
LIVE_TRADING_ENABLED = bool(_cfg.get("live_trading_enabled", False))
CLOB_SIGNING_MODE = _cfg.get("clob_signing_mode", "stub").strip().lower()

SIGMA_F = 2.0
SIGMA_C = 1.2

DATA_DIR         = Path("data")
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR          = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE         = LOG_DIR / "weatherbet.log"
DASHBOARD_FILE   = DATA_DIR / "dashboard.json"
STATE_FILE       = DATA_DIR / "state.json"
MARKETS_DIR      = DATA_DIR / "markets"
MARKETS_DIR.mkdir(exist_ok=True)
CALIBRATION_FILE = DATA_DIR / "calibration.json"

LOCATIONS = {
    "nyc":          {"lat": 40.7772,  "lon":  -73.8726, "name": "New York City", "station": "KLGA", "unit": "F", "region": "us"},
    "chicago":      {"lat": 41.9742,  "lon":  -87.9073, "name": "Chicago",       "station": "KORD", "unit": "F", "region": "us"},
    "miami":        {"lat": 25.7959,  "lon":  -80.2870, "name": "Miami",         "station": "KMIA", "unit": "F", "region": "us"},
    "dallas":       {"lat": 32.8471,  "lon":  -96.8518, "name": "Dallas",        "station": "KDAL", "unit": "F", "region": "us"},
    "seattle":      {"lat": 47.4502,  "lon": -122.3088, "name": "Seattle",       "station": "KSEA", "unit": "F", "region": "us"},
    "atlanta":      {"lat": 33.6407,  "lon":  -84.4277, "name": "Atlanta",       "station": "KATL", "unit": "F", "region": "us"},
    "london":       {"lat": 51.5048,  "lon":    0.0495, "name": "London",        "station": "EGLC", "unit": "C", "region": "eu"},
    "paris":        {"lat": 48.9962,  "lon":    2.5979, "name": "Paris",         "station": "LFPG", "unit": "C", "region": "eu"},
    "munich":       {"lat": 48.3537,  "lon":   11.7750, "name": "Munich",        "station": "EDDM", "unit": "C", "region": "eu"},
    "ankara":       {"lat": 40.1281,  "lon":   32.9951, "name": "Ankara",        "station": "LTAC", "unit": "C", "region": "eu"},
    "seoul":        {"lat": 37.4691,  "lon":  126.4505, "name": "Seoul",         "station": "RKSI", "unit": "C", "region": "asia"},
    "tokyo":        {"lat": 35.7647,  "lon":  140.3864, "name": "Tokyo",         "station": "RJTT", "unit": "C", "region": "asia"},
    "shanghai":     {"lat": 31.1443,  "lon":  121.8083, "name": "Shanghai",      "station": "ZSPD", "unit": "C", "region": "asia"},
    "singapore":    {"lat":  1.3502,  "lon":  103.9940, "name": "Singapore",     "station": "WSSS", "unit": "C", "region": "asia"},
    "lucknow":      {"lat": 26.7606,  "lon":   80.8893, "name": "Lucknow",       "station": "VILK", "unit": "C", "region": "asia"},
    "tel-aviv":     {"lat": 32.0114,  "lon":   34.8867, "name": "Tel Aviv",      "station": "LLBG", "unit": "C", "region": "asia"},
    "toronto":      {"lat": 43.6772,  "lon":  -79.6306, "name": "Toronto",       "station": "CYYZ", "unit": "C", "region": "ca"},
    "sao-paulo":    {"lat": -23.4356, "lon":  -46.4731, "name": "Sao Paulo",     "station": "SBGR", "unit": "C", "region": "sa"},
    "buenos-aires": {"lat": -34.8222, "lon":  -58.5358, "name": "Buenos Aires",  "station": "SAEZ", "unit": "C", "region": "sa"},
    "wellington":   {"lat": -41.3272, "lon":  174.8052, "name": "Wellington",    "station": "NZWN", "unit": "C", "region": "oc"},
}

TIMEZONES = {
    "nyc": "America/New_York", "chicago": "America/Chicago",
    "miami": "America/New_York", "dallas": "America/Chicago",
    "seattle": "America/Los_Angeles", "atlanta": "America/New_York",
    "london": "Europe/London", "paris": "Europe/Paris",
    "munich": "Europe/Berlin", "ankara": "Europe/Istanbul",
    "seoul": "Asia/Seoul", "tokyo": "Asia/Tokyo",
    "shanghai": "Asia/Shanghai", "singapore": "Asia/Singapore",
    "lucknow": "Asia/Kolkata", "tel-aviv": "Asia/Jerusalem",
    "toronto": "America/Toronto", "sao-paulo": "America/Sao_Paulo",
    "buenos-aires": "America/Argentina/Buenos_Aires", "wellington": "Pacific/Auckland",
}

MONTHS = ["january","february","march","april","may","june",
          "july","august","september","october","november","december"]
API_FAILURE_COUNTS = {}

# =============================================================================
# LOGGING
# =============================================================================

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
        # Logging failures should not stop trading logic.
        pass

# =============================================================================
# MATH
# =============================================================================

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bucket_prob(forecast, t_low, t_high, sigma=None):
    """For regular buckets — exact match. For edge buckets — normal distribution."""
    s = 2.0 if sigma is None else float(sigma)
    if s <= 0:
        if t_low == -999:
            return 1.0 if float(forecast) <= t_high else 0.0
        if t_high == 999:
            return 1.0 if float(forecast) >= t_low else 0.0
        return 1.0 if in_bucket(forecast, t_low, t_high) else 0.0
    if t_low == -999:
        return norm_cdf((t_high - float(forecast)) / s)
    if t_high == 999:
        return 1.0 - norm_cdf((t_low - float(forecast)) / s)
    return 1.0 if in_bucket(forecast, t_low, t_high) else 0.0

def calc_ev(p, price):
    if price <= 0 or price >= 1: return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)

def calc_kelly(p, price):
    if price <= 0 or price >= 1: return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * KELLY_FRACTION, 1.0), 4)

def bet_size(kelly, balance):
    raw = kelly * balance
    return round(min(raw, MAX_BET), 2)


def blend_forecast(ecmwf, hrrr, city_slug):
    """Inverse-variance weighted blend of ECMWF and HRRR forecasts."""
    sources = []
    if ecmwf is not None:
        sources.append((ecmwf, get_sigma(city_slug, "ecmwf")))
    if hrrr is not None:
        sources.append((hrrr, get_sigma(city_slug, "hrrr")))

    if not sources:
        return None, None
    if len(sources) == 1:
        return sources[0][0], sources[0][1]

    weights = [1.0 / (s ** 2) for _, s in sources]
    total_w = sum(weights)
    blended_temp = sum(t * w for (t, _), w in zip(sources, weights)) / total_w
    blended_sig = math.sqrt(1.0 / total_w)

    unit = LOCATIONS[city_slug]["unit"]
    if unit == "F":
        return round(blended_temp), round(blended_sig, 3)
    return round(blended_temp, 1), round(blended_sig, 3)

# =============================================================================
# CALIBRATION
# =============================================================================

_cal: dict = {}

def load_cal():
    if CALIBRATION_FILE.exists():
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    return {}

def get_sigma(city_slug, source="ecmwf"):
    key = f"{city_slug}_{source}"
    if key in _cal:
        return _cal[key]["sigma"]
    return SIGMA_F if LOCATIONS[city_slug]["unit"] == "F" else SIGMA_C

def run_calibration(markets):
    """Recalculates sigma from resolved markets."""
    resolved = [m for m in markets if m.get("resolved") and m.get("actual_temp") is not None]
    cal = load_cal()
    updated = []

    for source in ["ecmwf", "hrrr", "metar"]:
        for city in set(m["city"] for m in resolved):
            group = [m for m in resolved if m["city"] == city]
            errors = []
            for m in group:
                snap = next((s for s in reversed(m.get("forecast_snapshots", []))
                             if s["source"] == source), None)
                if snap and snap.get("temp") is not None:
                    errors.append(abs(snap["temp"] - m["actual_temp"]))
            if len(errors) < CALIBRATION_MIN:
                continue
            rmse = math.sqrt(sum(e ** 2 for e in errors) / len(errors))
            key  = f"{city}_{source}"
            old  = cal.get(key, {}).get("sigma", SIGMA_F if LOCATIONS[city]["unit"] == "F" else SIGMA_C)
            new  = round(rmse, 3)
            cal[key] = {"sigma": new, "n": len(errors), "updated_at": datetime.now(timezone.utc).isoformat()}
            if abs(new - old) > 0.05:
                updated.append(f"{LOCATIONS[city]['name']} {source}: {old:.2f}->{new:.2f}")

    CALIBRATION_FILE.write_text(json.dumps(cal, indent=2), encoding="utf-8")
    if updated:
        log_event("INFO", f"[CAL] {', '.join(updated)}", updated=updated)
    return cal

# =============================================================================
# FORECASTS
# =============================================================================

def get_ecmwf(city_slug, dates):
    """ECMWF via Open-Meteo with bias correction. For all cities."""
    loc = LOCATIONS[city_slug]
    unit = loc["unit"]
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    result = {}
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
        f"&forecast_days=7&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models=ecmwf_ifs025&bias_correction=true"
    )
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data:
                for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                    if date in dates and temp is not None:
                        result[date] = round(temp, 1) if unit == "C" else round(temp)
            track_api_result("open_meteo_ecmwf", True)
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                track_api_result("open_meteo_ecmwf", False, str(e))
                log_event("WARNING", f"[ECMWF] {city_slug}: {e}", city=city_slug, source="ecmwf")
    return result

def get_hrrr(city_slug, dates):
    """HRRR via Open-Meteo. US cities only, up to 48h horizon."""
    loc = LOCATIONS[city_slug]
    if loc["region"] != "us":
        return {}
    result = {}
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit=fahrenheit"
        f"&forecast_days=3&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models=gfs_seamless"  # HRRR+GFS seamless — best option for US
    )
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data:
                for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                    if date in dates and temp is not None:
                        result[date] = round(temp)
            track_api_result("open_meteo_hrrr", True)
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                track_api_result("open_meteo_hrrr", False, str(e))
                log_event("WARNING", f"[HRRR] {city_slug}: {e}", city=city_slug, source="hrrr")
    return result

def get_metar(city_slug):
    """Current observed temperature from METAR station. D+0 only."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=json"
        data = requests.get(url, timeout=(5, 8)).json()
        if data and isinstance(data, list):
            temp_c = data[0].get("temp")
            if temp_c is not None:
                track_api_result("aviationweather_metar", True)
                if unit == "F":
                    return round(float(temp_c) * 9/5 + 32)
                return round(float(temp_c), 1)
        track_api_result("aviationweather_metar", True)
    except Exception as e:
        track_api_result("aviationweather_metar", False, str(e))
        log_event("WARNING", f"[METAR] {city_slug}: {e}", city=city_slug, source="metar")
    return None

def get_actual_temp(city_slug, date_str):
    """Actual temperature via Visual Crossing for closed markets."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    vc_unit = "us" if unit == "F" else "metric"
    url = (
        f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
        f"/{station}/{date_str}/{date_str}"
        f"?unitGroup={vc_unit}&key={VC_KEY}&include=days&elements=tempmax"
    )
    try:
        data = requests.get(url, timeout=(5, 8)).json()
        days = data.get("days", [])
        if days and days[0].get("tempmax") is not None:
            track_api_result("visualcrossing", True)
            return round(float(days[0]["tempmax"]), 1)
        track_api_result("visualcrossing", True)
    except Exception as e:
        track_api_result("visualcrossing", False, str(e))
        log_event("WARNING", f"[VC] {city_slug} {date_str}: {e}", city=city_slug, date=date_str, source="vc")
    return None

def check_market_resolved(market_id):
    """
    Checks if the market closed on Polymarket and who won.
    Returns: None (still open), True (YES won), False (NO won)
    """
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=(5, 8))
        data = r.json()
        closed = data.get("closed", False)
        if not closed:
            return None
        # Check YES price — if ~1.0 then WIN, if ~0.0 then LOSS
        prices = json.loads(data.get("outcomePrices", "[0.5,0.5]"))
        yes_price = float(prices[0])
        if yes_price >= 0.95:
            track_api_result("polymarket_market", True)
            return True   # WIN
        elif yes_price <= 0.05:
            track_api_result("polymarket_market", True)
            return False  # LOSS
        track_api_result("polymarket_market", True)
        return None  # not yet determined
    except Exception as e:
        track_api_result("polymarket_market", False, str(e))
        log_event("WARNING", f"[RESOLVE] {market_id}: {e}", market_id=market_id)
    return None

# =============================================================================
# POLYMARKET
# =============================================================================

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


class PolymarketCLOBClient:
    """Minimal Polymarket CLOB REST client."""

    def __init__(self, base_url=None, api_key=None, timeout=(5, 10)):
        self.base_url = (base_url or CLOB_BASE_URL).rstrip("/")
        self.api_key = CLOB_API_KEY if api_key is None else api_key
        self.timeout = timeout

    def _headers(self):
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def get_json(self, path, params=None):
        url = f"{self.base_url}{path}"
        r = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def post_json(self, path, payload):
        url = f"{self.base_url}{path}"
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        r = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_orderbook(self, token_id):
        # CLOB read-only book endpoint.
        return self.get_json("/book", params={"token_id": token_id})

    def place_order(self, payload):
        # Endpoint path may vary by deployment; kept configurable via base URL.
        return self.post_json("/order", payload)

    def get_order_status(self, order_id):
        return self.get_json(f"/order/{order_id}")


def build_clob_order_payload(token_id, side, price, size):
    side_norm = side.lower().strip()
    if side_norm not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    payload = {
        "token_id": str(token_id),
        "side": side_norm,
        "price": round(float(price), 6),
        "size": round(float(size), 6),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return payload


def sign_clob_order_payload(payload, private_key, mode=None):
    """Signs an order payload.

    mode:
      - stub: deterministic local stub signature
      - eth_sign: personal_sign compatible signature via eth-account
    """
    mode = (mode or CLOB_SIGNING_MODE).lower()
    if not validate_private_key(private_key):
        raise ValueError("invalid private key")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    if mode == "stub":
        digest = hashlib.sha256((body + private_key).encode("utf-8")).hexdigest()
        return f"stub_{digest}"

    if mode == "eth_sign":
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct
        except Exception as e:
            raise RuntimeError("eth_sign mode requires eth-account package") from e
        msg = encode_defunct(text=body)
        signed = Account.sign_message(msg, private_key=private_key)
        return signed.signature.hex()

    raise ValueError("unsupported signing mode")


def verify_eth_sign_payload_signature(payload, signature_hex, expected_address):
    """Verifies personal_sign style signature for payload JSON."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except Exception as e:
        raise RuntimeError("signature verification requires eth-account package") from e

    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    msg = encode_defunct(text=body)
    recovered = Account.recover_message(msg, signature=signature_hex)
    return recovered.lower() == str(expected_address).lower()


def submit_clob_order(token_id, side, price, size, dry_run=True):
    payload = build_clob_order_payload(token_id, side, price, size)
    creds = load_wallet_credentials()
    signature = sign_clob_order_payload(payload, creds["private_key"])
    signed_payload = dict(payload)
    signed_payload["wallet_address"] = creds["wallet_address"]
    signed_payload["signature"] = signature

    if dry_run or not LIVE_TRADING_ENABLED:
        return {
            "mode": "dry_run",
            "live_enabled": LIVE_TRADING_ENABLED,
            "payload": signed_payload,
        }

    client = PolymarketCLOBClient()
    result = client.place_order(signed_payload)
    return {"mode": "live", "result": result}


def fetch_order_status(order_id):
    client = PolymarketCLOBClient()
    return client.get_order_status(order_id)


def wait_for_order_fill(order_id, timeout_sec=60, poll_interval=3):
    """Polls order status until filled/canceled/expired or timeout."""
    deadline = time.time() + max(1, int(timeout_sec))
    interval = max(1, int(poll_interval))
    last_status = None
    while time.time() < deadline:
        status = fetch_order_status(order_id)
        state = str(status.get("status", "")).lower()
        last_status = status
        if state in {"filled", "cancelled", "canceled", "expired", "rejected"}:
            return {"done": True, "status": status}
        time.sleep(interval)
    return {"done": False, "status": last_status}

def parse_temp_range(question):
    if not question: return None
    num = r'(-?\d+(?:\.\d+)?)'
    if re.search(r'or below', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or below', question, re.IGNORECASE)
        if m: return (-999.0, float(m.group(1)))
    if re.search(r'or higher', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or higher', question, re.IGNORECASE)
        if m: return (float(m.group(1)), 999.0)
    m = re.search(r'between ' + num + r'-' + num + r'[°]?[FC]', question, re.IGNORECASE)
    if m: return (float(m.group(1)), float(m.group(2)))
    m = re.search(r'be ' + num + r'[°]?[FC] on', question, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None

def hours_to_resolution(end_date_str):
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 3600)
    except Exception:
        return 999.0

def in_bucket(forecast, t_low, t_high):
    if t_low == t_high:
        return round(float(forecast)) == round(t_low)
    return t_low <= float(forecast) <= t_high

# =============================================================================
# MARKET DATA STORAGE
# Each market is stored in a separate file: data/markets/{city}_{date}.json
# =============================================================================

def market_path(city_slug, date_str):
    return MARKETS_DIR / f"{city_slug}_{date_str}.json"

def load_market(city_slug, date_str):
    p = market_path(city_slug, date_str)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None

def save_market(market):
    p = market_path(market["city"], market["date"])
    p.write_text(json.dumps(market, indent=2, ensure_ascii=False), encoding="utf-8")

def load_all_markets():
    markets = []
    for f in MARKETS_DIR.glob("*.json"):
        try:
            markets.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return markets


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

    - <24h: None (hold to resolution)
    - 24-48h: linearly from 0.85 -> 0.75
    - >=48h: 0.75
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
    # Keep stop in a sane range.
    loss_pct = min(max(loss_pct, 0.10), 0.35)
    return round(entry_price * (1.0 - loss_pct), 4)


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


def mask_secret(value, keep=4):
    """Masks secret values for safe console output."""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


def load_wallet_credentials():
    """Loads wallet credentials from env first, then config fallback."""
    env_pk = ""
    try:
        env_pk = __import__("os").environ.get("POLYGON_PRIVATE_KEY", "").strip()
    except Exception:
        env_pk = ""
    private_key = env_pk or POLYGON_PRIVATE_KEY
    wallet_address = POLYGON_WALLET_ADDRESS
    return {
        "private_key": private_key,
        "wallet_address": wallet_address,
    }


def validate_private_key(private_key):
    """Basic EVM private key format validation."""
    if not private_key:
        return False
    key = private_key.lower()
    if key.startswith("0x"):
        key = key[2:]
    return bool(re.fullmatch(r"[0-9a-f]{64}", key))


def wallet_status():
    creds = load_wallet_credentials()
    private_key = creds["private_key"]
    wallet_address = creds["wallet_address"]
    return {
        "has_private_key": bool(private_key),
        "private_key_valid": validate_private_key(private_key),
        "masked_private_key": mask_secret(private_key),
        "wallet_address": wallet_address,
    }

def new_market(city_slug, date_str, event, hours):
    loc = LOCATIONS[city_slug]
    return {
        "city":               city_slug,
        "city_name":          loc["name"],
        "date":               date_str,
        "unit":               loc["unit"],
        "station":            loc["station"],
        "event_end_date":     event.get("endDate", ""),
        "hours_at_discovery": round(hours, 1),
        "status":             "open",           # open | closed | resolved
        "position":           None,             # filled when position opens
        "actual_temp":        None,             # filled after resolution
        "resolved_outcome":   None,             # win / loss / no_position
        "pnl":                None,
        "forecast_snapshots": [],               # list of forecast snapshots
        "market_snapshots":   [],               # list of market price snapshots
        "all_outcomes":       [],               # all market buckets
        "created_at":         datetime.now(timezone.utc).isoformat(),
    }

# =============================================================================
# STATE (balance and open positions)
# =============================================================================

def load_state():
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

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

# =============================================================================
# CORE LOGIC
# =============================================================================

def take_forecast_snapshot(city_slug, dates):
    """Fetches forecasts from all sources and returns a snapshot."""
    now_str = datetime.now(timezone.utc).isoformat()
    ecmwf   = get_ecmwf(city_slug, dates)
    hrrr    = get_hrrr(city_slug, dates)
    today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    snapshots = {}
    for date in dates:
        snap = {
            "ts":    now_str,
            "ecmwf": ecmwf.get(date),
            "hrrr":  hrrr.get(date) if date <= (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d") else None,
            "metar": get_metar(city_slug) if date == today else None,
        }
        # Best forecast: HRRR for US D+0/D+1, otherwise ECMWF
        loc = LOCATIONS[city_slug]
        if loc["region"] == "us" and snap["hrrr"] is not None:
            snap["best"] = snap["hrrr"]
            snap["best_source"] = "hrrr"
        elif snap["ecmwf"] is not None:
            snap["best"] = snap["ecmwf"]
            snap["best_source"] = "ecmwf"
        else:
            snap["best"] = None
            snap["best_source"] = None
        snapshots[date] = snap
    return snapshots

def scan_and_update():
    """Main function of one cycle: updates forecasts, opens/closes positions."""
    global _cal
    now      = datetime.now(timezone.utc)
    state    = load_state()
    balance  = state["balance"]
    new_pos  = 0
    closed   = 0
    resolved = 0

    markets = load_all_markets()
    open_keys = {
        (m.get("city"), m.get("date"))
        for m in markets
        if (m.get("position") or {}).get("status") == "open"
    }
    daily_loss = get_today_realized_loss(markets, now=now)
    daily_limit = round(state.get("starting_balance", balance) * DAILY_LOSS_LIMIT_PCT, 2)
    if DAILY_LOSS_LIMIT_PCT > 0 and daily_loss >= daily_limit:
        log_event(
            "WARNING",
            f"[RISK] Daily loss limit reached ({daily_loss:.2f}/{daily_limit:.2f}) - scan skipped",
            daily_loss=daily_loss,
            daily_limit=daily_limit,
        )
        return

    for city_slug, loc in LOCATIONS.items():
        unit = loc["unit"]
        unit_sym = "F" if unit == "F" else "C"
        print(f"  -> {loc['name']}...", end=" ", flush=True)

        try:
            dates = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)]
            snapshots = take_forecast_snapshot(city_slug, dates)
            time.sleep(0.3)
        except Exception as e:
            print(f"skipped ({e})")
            continue

        for i, date in enumerate(dates):
            dt    = datetime.strptime(date, "%Y-%m-%d")
            event = get_polymarket_event(city_slug, MONTHS[dt.month - 1], dt.day, dt.year)
            if not event:
                continue

            end_date = event.get("endDate", "")
            hours    = hours_to_resolution(end_date) if end_date else 0
            horizon  = f"D+{i}"

            # Load or create market record
            mkt = load_market(city_slug, date)
            if mkt is None:
                if hours < MIN_HOURS or hours > MAX_HOURS:
                    continue
                mkt = new_market(city_slug, date, event, hours)

            # Skip if market already resolved
            if mkt["status"] == "resolved":
                continue

            # Update outcomes list — prices taken directly from event
            outcomes = []
            for market in event.get("markets", []):
                question = market.get("question", "")
                mid      = str(market.get("id", ""))
                volume   = float(market.get("volume", 0))
                rng      = parse_temp_range(question)
                if not rng:
                    continue
                try:
                    prices = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
                    bid = float(prices[0])
                    ask = float(prices[1]) if len(prices) > 1 else bid
                except Exception:
                    continue
                outcomes.append({
                    "question":  question,
                    "market_id": mid,
                    "range":     rng,
                    "bid":       round(bid, 4),
                    "ask":       round(ask, 4),
                    "price":     round(bid, 4),   # for compatibility
                    "spread":    round(ask - bid, 4),
                    "volume":    round(volume, 0),
                })

            outcomes.sort(key=lambda x: x["range"][0])
            mkt["all_outcomes"] = outcomes

            # Forecast snapshot
            snap = snapshots.get(date, {})
            forecast_snap = {
                "ts":          snap.get("ts"),
                "horizon":     horizon,
                "hours_left":  round(hours, 1),
                "ecmwf":       snap.get("ecmwf"),
                "hrrr":        snap.get("hrrr"),
                "metar":       snap.get("metar"),
                "best":        snap.get("best"),
                "best_source": snap.get("best_source"),
            }
            mkt["forecast_snapshots"].append(forecast_snap)

            # Market price snapshot
            top = max(outcomes, key=lambda x: x["price"]) if outcomes else None
            market_snap = {
                "ts":       snap.get("ts"),
                "top_bucket": f"{top['range'][0]}-{top['range'][1]}{unit_sym}" if top else None,
                "top_price":  top["price"] if top else None,
            }
            mkt["market_snapshots"].append(market_snap)

            forecast_temp = snap.get("best")
            best_source   = snap.get("best_source")

            # --- STOP-LOSS AND TRAILING STOP ---
            if mkt.get("position") and mkt["position"].get("status") == "open":
                pos = mkt["position"]
                current_price = None
                for o in outcomes:
                    if o["market_id"] == pos["market_id"]:
                        current_price = o["price"]
                        break

                if current_price is not None:
                    current_price = o.get("bid", current_price)  # sell at bid
                    entry = pos["entry_price"]
                    sigma = pos.get("sigma")
                    stop  = pos.get("stop_price", calc_dynamic_stop_price(entry, sigma, unit))  # dynamic stop

                    # Trailing: if up 20%+ — move stop to breakeven
                    if current_price >= entry * 1.20 and stop < entry:
                        pos["stop_price"] = entry
                        pos["trailing_activated"] = True

                    # Check stop
                    if current_price <= stop:
                        pnl = round((current_price - entry) * pos["shares"], 2)
                        balance += pos["cost"] + pnl
                        pos["closed_at"]    = snap.get("ts")
                        pos["close_reason"] = "stop_loss" if current_price < entry else "trailing_stop"
                        pos["exit_price"]   = current_price
                        pos["pnl"]          = pnl
                        pos["status"]       = "closed"
                        closed += 1
                        reason = "STOP" if current_price < entry else "TRAILING BE"
                        print(f"  [{reason}] {loc['name']} {date} | entry ${entry:.3f} exit ${current_price:.3f} | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")
                        level = "WARNING" if current_price < entry else "INFO"
                        log_event(
                            level,
                            f"[{reason}] {loc['name']} {date} | entry ${entry:.3f} exit ${current_price:.3f} | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}",
                            city=city_slug,
                            date=date,
                            reason=pos["close_reason"],
                            pnl=pnl,
                        )
                        if current_price < entry:
                            send_discord_notification(
                                f"STOP LOSS: {loc['name']} {date} | entry ${entry:.3f} -> exit ${current_price:.3f} | PnL {pnl:+.2f}"
                            )

            # --- CLOSE POSITION if forecast shifted 2+ degrees ---
            if mkt.get("position") and forecast_temp is not None:
                pos = mkt["position"]
                old_bucket_low  = pos["bucket_low"]
                old_bucket_high = pos["bucket_high"]
                # 2-degree buffer — avoid closing on small forecast fluctuations
                unit = loc["unit"]
                buffer = 2.0 if unit == "F" else 1.0
                mid_bucket = (old_bucket_low + old_bucket_high) / 2 if old_bucket_low != -999 and old_bucket_high != 999 else forecast_temp
                forecast_far = abs(forecast_temp - mid_bucket) > (abs(mid_bucket - old_bucket_low) + buffer)
                if not in_bucket(forecast_temp, old_bucket_low, old_bucket_high) and forecast_far:
                    current_price = None
                    for o in outcomes:
                        if o["market_id"] == pos["market_id"]:
                            current_price = o["price"]
                            break
                    if current_price is not None:
                        pnl = round((current_price - pos["entry_price"]) * pos["shares"], 2)
                        balance += pos["cost"] + pnl
                        mkt["position"]["closed_at"]    = snap.get("ts")
                        mkt["position"]["close_reason"] = "forecast_changed"
                        mkt["position"]["exit_price"]   = current_price
                        mkt["position"]["pnl"]          = pnl
                        mkt["position"]["status"]       = "closed"
                        closed += 1
                        print(f"  [CLOSE] {loc['name']} {date} — forecast changed | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")
                        log_event(
                            "INFO",
                            f"[CLOSE] {loc['name']} {date} — forecast changed | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}",
                            city=city_slug,
                            date=date,
                            reason="forecast_changed",
                            pnl=pnl,
                        )

            # --- OPEN POSITION ---
            if not mkt.get("position") and forecast_temp is not None and hours >= MIN_HOURS:
                if (city_slug, date) in open_keys:
                    log_event(
                        "INFO",
                        f"[SKIP] Correlation guard: existing open position for {loc['name']} {date}",
                        city=city_slug,
                        date=date,
                        reason="correlation_guard",
                    )
                    continue
                sigma = get_sigma(city_slug, best_source or "ecmwf")
                best_signal = None

                # Find exactly ONE bucket that matches the forecast
                # If forecast doesn't fit any bucket cleanly — skip this market
                matched_bucket = None
                for o in outcomes:
                    t_low, t_high = o["range"]
                    if in_bucket(forecast_temp, t_low, t_high):
                        matched_bucket = o
                        break

                if matched_bucket:
                    o = matched_bucket
                    t_low, t_high = o["range"]
                    volume = o["volume"]
                    bid    = o.get("bid", o["price"])
                    ask    = o.get("ask", o["price"])
                    spread = o.get("spread", 0)

                    # All filters — if any fails, skip this market entirely
                    if volume >= MIN_VOLUME:
                        p  = bucket_prob(forecast_temp, t_low, t_high, sigma)
                        ev = calc_ev(p, ask)
                        if ev >= MIN_EV:
                            kelly = calc_kelly(p, ask)
                            size  = bet_size(kelly, balance)
                            if size >= 0.50:
                                best_signal = {
                                    "market_id":    o["market_id"],
                                    "question":     o["question"],
                                    "bucket_low":   t_low,
                                    "bucket_high":  t_high,
                                    "entry_price":  ask,
                                    "bid_at_entry": bid,
                                    "spread":       spread,
                                    "shares":       round(size / ask, 2),
                                    "cost":         size,
                                    "p":            round(p, 4),
                                    "ev":           round(ev, 4),
                                    "kelly":        round(kelly, 4),
                                    "forecast_temp":forecast_temp,
                                    "forecast_src": best_source,
                                    "sigma":        sigma,
                                    "opened_at":    snap.get("ts"),
                                    "status":       "open",
                                    "pnl":          None,
                                    "exit_price":   None,
                                    "close_reason": None,
                                    "closed_at":    None,
                                }

                if best_signal:
                    # Fetch real bestAsk from Polymarket API for accurate entry price
                    skip_position = False
                    try:
                        r = requests.get(f"https://gamma-api.polymarket.com/markets/{best_signal['market_id']}", timeout=(3, 5))
                        mdata = r.json()
                        track_api_result("polymarket_market", True)
                        real_ask = float(mdata.get("bestAsk", best_signal["entry_price"]))
                        real_bid = float(mdata.get("bestBid", best_signal["bid_at_entry"]))
                        real_spread = round(real_ask - real_bid, 4)
                        # Re-check slippage and price with real values
                        if real_spread > MAX_SLIPPAGE or real_ask >= MAX_PRICE:
                            print(f"  [SKIP] {loc['name']} {date} — real ask ${real_ask:.3f} spread ${real_spread:.3f}")
                            skip_position = True
                        else:
                            best_signal["entry_price"]  = real_ask
                            best_signal["bid_at_entry"] = real_bid
                            best_signal["spread"]       = real_spread
                            best_signal["shares"]       = round(best_signal["cost"] / real_ask, 2)
                            best_signal["ev"]           = round(calc_ev(best_signal["p"], real_ask), 4)
                    except Exception as e:
                        track_api_result("polymarket_market", False, str(e))
                        print(f"  [WARN] Could not fetch real ask for {best_signal['market_id']}: {e}")
                        log_event(
                            "WARNING",
                            f"[WARN] Could not fetch real ask for {best_signal['market_id']}: {e}",
                            market_id=best_signal["market_id"],
                        )

                    if not skip_position and best_signal["entry_price"] < MAX_PRICE:
                        balance -= best_signal["cost"]
                        mkt["position"] = best_signal
                        open_keys.add((city_slug, date))
                        state["total_trades"] += 1
                        new_pos += 1
                        bucket_label = f"{best_signal['bucket_low']}-{best_signal['bucket_high']}{unit_sym}"
                        print(f"  [BUY]  {loc['name']} {horizon} {date} | {bucket_label} | "
                              f"${best_signal['entry_price']:.3f} | EV {best_signal['ev']:+.2f} | "
                              f"${best_signal['cost']:.2f} ({best_signal['forecast_src'].upper()})")
                        log_event(
                            "INFO",
                            f"[BUY] {loc['name']} {horizon} {date} | {bucket_label} | "
                            f"${best_signal['entry_price']:.3f} | EV {best_signal['ev']:+.2f} | "
                            f"${best_signal['cost']:.2f} ({best_signal['forecast_src'].upper()})",
                            city=city_slug,
                            date=date,
                            horizon=horizon,
                            market_id=best_signal["market_id"],
                            ev=best_signal["ev"],
                            cost=best_signal["cost"],
                        )
                        send_discord_notification(
                            f"NEW POSITION: {loc['name']} {date} {bucket_label} | entry ${best_signal['entry_price']:.3f} | size ${best_signal['cost']:.2f} | EV {best_signal['ev']:+.2f}"
                        )

            # Market closed by time
            if hours < 0.5 and mkt["status"] == "open":
                mkt["status"] = "closed"

            save_market(mkt)
            time.sleep(0.1)

        print("ok")

    # --- AUTO-RESOLUTION ---
    for mkt in load_all_markets():
        if mkt["status"] == "resolved":
            continue

        pos = mkt.get("position")
        if not pos or pos.get("status") != "open":
            continue

        market_id = pos.get("market_id")
        if not market_id:
            continue

        # Check if market closed on Polymarket
        won = check_market_resolved(market_id)
        if won is None:
            continue  # market still open

        # Market closed — record result
        price  = pos["entry_price"]
        size   = pos["cost"]
        shares = pos["shares"]
        pnl    = round(shares * (1 - price), 2) if won else round(-size, 2)

        balance += size + pnl
        pos["exit_price"]   = 1.0 if won else 0.0
        pos["pnl"]          = pnl
        pos["close_reason"] = "resolved"
        pos["closed_at"]    = now.isoformat()
        pos["status"]       = "closed"
        mkt["pnl"]          = pnl
        mkt["status"]       = "resolved"
        mkt["resolved_outcome"] = "win" if won else "loss"

        if won:
            state["wins"] += 1
        else:
            state["losses"] += 1

        result = "WIN" if won else "LOSS"
        print(f"  [{result}] {mkt['city_name']} {mkt['date']} | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")
        log_event(
            "INFO",
            f"[{result}] {mkt['city_name']} {mkt['date']} | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}",
            city=mkt["city"],
            date=mkt["date"],
            result=mkt["resolved_outcome"],
            pnl=pnl,
        )
        resolved += 1

        save_market(mkt)
        time.sleep(0.3)

    state["balance"]      = round(balance, 2)
    state["peak_balance"] = max(state.get("peak_balance", balance), balance)
    save_state(state)

    # Run calibration if enough data collected
    all_mkts = load_all_markets()
    resolved_count = len([m for m in all_mkts if m["status"] == "resolved"])
    if resolved_count >= CALIBRATION_MIN:
        global _cal
        _cal = run_calibration(all_mkts)

    return new_pos, closed, resolved

# =============================================================================
# REPORT
# =============================================================================

def print_status():
    state    = load_state()
    markets  = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]

    bal     = state["balance"]
    start   = state["starting_balance"]
    ret_pct = (bal - start) / start * 100
    wins    = state["wins"]
    losses  = state["losses"]
    total   = wins + losses

    print(f"\n{'='*55}")
    print(f"  WEATHERBET — STATUS")
    print(f"{'='*55}")
    print(f"  Balance:     ${bal:,.2f}  (start ${start:,.2f}, {'+'if ret_pct>=0 else ''}{ret_pct:.1f}%)")
    print(f"  Trades:      {total} | W: {wins} | L: {losses} | WR: {wins/total:.0%}" if total else "  No trades yet")
    print(f"  Open:        {len(open_pos)}")
    print(f"  Resolved:    {len(resolved)}")

    if open_pos:
        print(f"\n  Open positions:")
        total_unrealized = 0.0
        for m in open_pos:
            pos      = m["position"]
            unit_sym = "F" if m["unit"] == "F" else "C"
            label    = f"{pos['bucket_low']}-{pos['bucket_high']}{unit_sym}"

            # Current price from latest market snapshot
            current_price = pos["entry_price"]
            snaps = m.get("market_snapshots", [])
            if snaps:
                # Find our bucket price in all_outcomes
                for o in m.get("all_outcomes", []):
                    if o["market_id"] == pos["market_id"]:
                        current_price = o["price"]
                        break

            unrealized = round((current_price - pos["entry_price"]) * pos["shares"], 2)
            total_unrealized += unrealized
            pnl_str = f"{'+'if unrealized>=0 else ''}{unrealized:.2f}"

            print(f"    {m['city_name']:<16} {m['date']} | {label:<14} | "
                  f"entry ${pos['entry_price']:.3f} -> ${current_price:.3f} | "
                  f"PnL: {pnl_str} | {pos['forecast_src'].upper()}")

        sign = "+" if total_unrealized >= 0 else ""
        print(f"\n  Unrealized PnL: {sign}{total_unrealized:.2f}")

    print(f"{'='*55}\n")

def print_report():
    markets  = load_all_markets()
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]

    print(f"\n{'='*55}")
    print(f"  WEATHERBET — FULL REPORT")
    print(f"{'='*55}")

    if not resolved:
        print("  No resolved markets yet.")
        return

    total_pnl = sum(m["pnl"] for m in resolved)
    wins      = [m for m in resolved if m["resolved_outcome"] == "win"]
    losses    = [m for m in resolved if m["resolved_outcome"] == "loss"]

    print(f"\n  Total resolved: {len(resolved)}")
    print(f"  Wins:           {len(wins)} | Losses: {len(losses)}")
    print(f"  Win rate:       {len(wins)/len(resolved):.0%}")
    print(f"  Total PnL:      {'+'if total_pnl>=0 else ''}{total_pnl:.2f}")

    print(f"\n  By city:")
    for city in sorted(set(m["city"] for m in resolved)):
        group = [m for m in resolved if m["city"] == city]
        w     = len([m for m in group if m["resolved_outcome"] == "win"])
        pnl   = sum(m["pnl"] for m in group)
        name  = LOCATIONS[city]["name"]
        print(f"    {name:<16} {w}/{len(group)} ({w/len(group):.0%})  PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")

    print(f"\n  Market details:")
    for m in sorted(resolved, key=lambda x: x["date"]):
        pos      = m.get("position", {})
        unit_sym = "F" if m["unit"] == "F" else "C"
        snaps    = m.get("forecast_snapshots", [])
        first_fc = snaps[0]["best"] if snaps else None
        last_fc  = snaps[-1]["best"] if snaps else None
        label    = f"{pos.get('bucket_low')}-{pos.get('bucket_high')}{unit_sym}" if pos else "no position"
        result   = m["resolved_outcome"].upper()
        pnl_str  = f"{'+'if m['pnl']>=0 else ''}{m['pnl']:.2f}" if m["pnl"] is not None else "-"
        fc_str   = f"forecast {first_fc}->{last_fc}{unit_sym}" if first_fc else "no forecast"
        actual   = f"actual {m['actual_temp']}{unit_sym}" if m["actual_temp"] else ""
        print(f"    {m['city_name']:<16} {m['date']} | {label:<14} | {fc_str} | {actual} | {result} {pnl_str}")

    print(f"{'='*55}\n")


def export_dashboard_data():
    """Generates data/dashboard.json for local dashboard usage."""
    state = load_state()
    markets = load_all_markets()
    open_pos = [m for m in markets if (m.get("position") or {}).get("status") == "open"]
    resolved = [m for m in markets if m.get("status") == "resolved" and m.get("pnl") is not None]

    open_positions = []
    for m in open_pos:
        pos = m["position"]
        current_price = pos["entry_price"]
        for o in m.get("all_outcomes", []):
            if o.get("market_id") == pos.get("market_id"):
                current_price = o.get("bid", o.get("price", current_price))
                break
        unrealized = round((current_price - pos["entry_price"]) * pos["shares"], 2)
        open_positions.append(
            {
                "city": m.get("city"),
                "city_name": m.get("city_name"),
                "date": m.get("date"),
                "bucket_low": pos.get("bucket_low"),
                "bucket_high": pos.get("bucket_high"),
                "entry_price": pos.get("entry_price"),
                "current_price": current_price,
                "shares": pos.get("shares"),
                "cost": pos.get("cost"),
                "unrealized_pnl": unrealized,
                "forecast_source": pos.get("forecast_src"),
            }
        )

    total_pnl = round(sum(float(m.get("pnl", 0)) for m in resolved), 2)
    wins = len([m for m in resolved if m.get("resolved_outcome") == "win"])
    losses = len([m for m in resolved if m.get("resolved_outcome") == "loss"])

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "summary": {
            "open_count": len(open_pos),
            "resolved_count": len(resolved),
            "wins": wins,
            "losses": losses,
            "total_realized_pnl": total_pnl,
        },
        "open_positions": open_positions,
        "recent_resolved": sorted(
            [
                {
                    "city": m.get("city"),
                    "city_name": m.get("city_name"),
                    "date": m.get("date"),
                    "pnl": m.get("pnl"),
                    "result": m.get("resolved_outcome"),
                }
                for m in resolved
            ],
            key=lambda x: x["date"] or "",
            reverse=True,
        )[:30],
    }

    DASHBOARD_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log_event("INFO", "[DASHBOARD] data/dashboard.json exported", path=str(DASHBOARD_FILE))
    return DASHBOARD_FILE

# =============================================================================
# MAIN LOOP
# =============================================================================

MONITOR_INTERVAL = 600  # monitor positions every 10 minutes

def monitor_positions():
    """Quick stop check on open positions without full scan."""
    markets  = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    if not open_pos:
        return 0

    state   = load_state()
    balance = state["balance"]
    closed  = 0

    for mkt in open_pos:
        pos = mkt["position"]
        mid = pos["market_id"]

        # Fetch real bestBid from Polymarket API — actual sell price
        current_price = None
        try:
            r = requests.get(f"https://gamma-api.polymarket.com/markets/{mid}", timeout=(3, 5))
            mdata = r.json()
            track_api_result("polymarket_market", True)
            best_bid = mdata.get("bestBid")
            if best_bid is not None:
                current_price = float(best_bid)
        except Exception as e:
            track_api_result("polymarket_market", False, str(e))

        # Fallback to cached price if API failed
        if current_price is None:
            for o in mkt.get("all_outcomes", []):
                if o["market_id"] == mid:
                    current_price = o.get("bid", o["price"])
                    break

        if current_price is None:
            continue

        entry = pos["entry_price"]
        stop  = pos.get("stop_price", calc_dynamic_stop_price(entry, pos.get("sigma"), mkt.get("unit", "F")))
        city_name = LOCATIONS.get(mkt["city"], {}).get("name", mkt["city"])

        # Hours left to resolution
        end_date = mkt.get("event_end_date", "")
        hours_left = hours_to_resolution(end_date) if end_date else 999.0

        # Continuous take-profit threshold based on hours to resolution
        take_profit = calc_take_profit_threshold(hours_left)

        # Trailing: if up 20%+ — move stop to breakeven
        if current_price >= entry * 1.20 and stop < entry:
            pos["stop_price"] = entry
            pos["trailing_activated"] = True
            print(f"  [TRAILING] {city_name} {mkt['date']} — stop moved to breakeven ${entry:.3f}")
            log_event(
                "INFO",
                f"[TRAILING] {city_name} {mkt['date']} — stop moved to breakeven ${entry:.3f}",
                city=mkt["city"],
                date=mkt["date"],
                stop_price=entry,
            )

        # Check take-profit
        take_triggered = take_profit is not None and current_price >= take_profit
        # Check stop
        stop_triggered = current_price <= stop

        if take_triggered or stop_triggered:
            pnl = round((current_price - entry) * pos["shares"], 2)
            balance += pos["cost"] + pnl
            pos["closed_at"]    = datetime.now(timezone.utc).isoformat()
            if take_triggered:
                pos["close_reason"] = "take_profit"
                reason = "TAKE"
            elif current_price < entry:
                pos["close_reason"] = "stop_loss"
                reason = "STOP"
            else:
                pos["close_reason"] = "trailing_stop"
                reason = "TRAILING BE"
            pos["exit_price"]   = current_price
            pos["pnl"]          = pnl
            pos["status"]       = "closed"
            closed += 1
            print(f"  [{reason}] {city_name} {mkt['date']} | entry ${entry:.3f} exit ${current_price:.3f} | {hours_left:.0f}h left | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")
            log_event(
                "WARNING" if pos["close_reason"] == "stop_loss" else "INFO",
                f"[{reason}] {city_name} {mkt['date']} | entry ${entry:.3f} exit ${current_price:.3f} | {hours_left:.0f}h left | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}",
                city=mkt["city"],
                date=mkt["date"],
                reason=pos["close_reason"],
                pnl=pnl,
            )
            if pos["close_reason"] == "stop_loss":
                send_discord_notification(
                    f"STOP LOSS: {city_name} {mkt['date']} | entry ${entry:.3f} -> exit ${current_price:.3f} | PnL {pnl:+.2f}"
                )
            save_market(mkt)

    if closed:
        state["balance"] = round(balance, 2)
        save_state(state)

    return closed


def run_loop():
    global _cal
    _cal = load_cal()

    print(f"\n{'='*55}")
    print(f"  WEATHERBET — STARTING")
    print(f"{'='*55}")
    print(f"  Cities:     {len(LOCATIONS)}")
    print(f"  Balance:    ${BALANCE:,.0f} | Max bet: ${MAX_BET}")
    print(f"  Scan:       {SCAN_INTERVAL//60} min | Monitor: {MONITOR_INTERVAL//60} min")
    print(f"  Sources:    ECMWF + HRRR(US) + METAR(D+0)")
    print(f"  Data:       {DATA_DIR.resolve()}")
    print(f"  Ctrl+C to stop\n")

    last_full_scan = 0

    while True:
        now_ts  = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Full scan once per hour
        if now_ts - last_full_scan >= SCAN_INTERVAL:
            print(f"[{now_str}] full scan...")
            try:
                new_pos, closed, resolved = scan_and_update()
                state = load_state()
                print(f"  balance: ${state['balance']:,.2f} | "
                      f"new: {new_pos} | closed: {closed} | resolved: {resolved}")
                last_full_scan = time.time()
            except KeyboardInterrupt:
                print(f"\n  Stopping — saving state...")
                save_state(load_state())
                print(f"  Done. Bye!")
                break
            except requests.exceptions.ConnectionError:
                print(f"  Connection lost — waiting 60 sec")
                time.sleep(60)
                continue
            except Exception as e:
                print(f"  Error: {e} — waiting 60 sec")
                time.sleep(60)
                continue
        else:
            # Quick stop monitoring
            print(f"[{now_str}] monitoring positions...")
            try:
                stopped = monitor_positions()
                if stopped:
                    state = load_state()
                    print(f"  balance: ${state['balance']:,.2f}")
            except Exception as e:
                print(f"  Monitor error: {e}")

        try:
            time.sleep(MONITOR_INTERVAL)
        except KeyboardInterrupt:
            print(f"\n  Stopping — saving state...")
            save_state(load_state())
            print(f"  Done. Bye!")
            break

# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_loop()
    elif cmd == "status":
        _cal = load_cal()
        print_status()
    elif cmd == "report":
        _cal = load_cal()
        print_report()
    elif cmd == "dashboard":
        export_path = export_dashboard_data()
        html_path = Path("sim_dashboard_repost.html").resolve()
        print(f"Dashboard data exported: {export_path}")
        if html_path.exists():
            webbrowser.open(html_path.as_uri())
            print(f"Opened: {html_path}")
        else:
            print("sim_dashboard_repost.html not found.")
    elif cmd == "clob-book":
        token_id = sys.argv[2] if len(sys.argv) > 2 else ""
        if not token_id:
            print("Usage: python bot_v2.py clob-book <token_id>")
            sys.exit(1)
        client = PolymarketCLOBClient()
        try:
            book = client.get_orderbook(token_id)
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            best_bid = bids[0]["price"] if bids else None
            best_ask = asks[0]["price"] if asks else None
            print(f"CLOB token_id={token_id}")
            print(f"  best_bid: {best_bid}")
            print(f"  best_ask: {best_ask}")
        except Exception as e:
            log_event("ERROR", f"[CLOB] Failed to fetch orderbook: {e}", token_id=token_id)
            print(f"CLOB error: {e}")
    elif cmd == "wallet-status":
        status = wallet_status()
        print("Wallet status:")
        print(f"  private key set:   {status['has_private_key']}")
        print(f"  private key valid: {status['private_key_valid']}")
        print(f"  private key:       {status['masked_private_key'] or '-'}")
        print(f"  wallet address:    {status['wallet_address'] or '-'}")
        print(f"  signing mode:      {CLOB_SIGNING_MODE}")
    elif cmd == "clob-order":
        if len(sys.argv) < 6:
            print("Usage: python bot_v2.py clob-order <token_id> <buy|sell> <price> <size> [--live]")
            sys.exit(1)
        token_id = sys.argv[2]
        side = sys.argv[3]
        price = sys.argv[4]
        size = sys.argv[5]
        dry_run = "--live" not in sys.argv[6:]
        try:
            res = submit_clob_order(token_id, side, price, size, dry_run=dry_run)
            print(json.dumps(res, indent=2, ensure_ascii=False))
        except Exception as e:
            log_event("ERROR", f"[CLOB] order submit failed: {e}", token_id=token_id, side=side)
            print(f"CLOB order error: {e}")
    elif cmd == "clob-sign-check":
        if len(sys.argv) < 6:
            print("Usage: python bot_v2.py clob-sign-check <token_id> <buy|sell> <price> <size>")
            sys.exit(1)
        token_id = sys.argv[2]
        side = sys.argv[3]
        price = sys.argv[4]
        size = sys.argv[5]
        creds = load_wallet_credentials()
        try:
            payload = build_clob_order_payload(token_id, side, price, size)
            signature = sign_clob_order_payload(payload, creds["private_key"], mode="eth_sign")
            ok = verify_eth_sign_payload_signature(payload, signature, creds["wallet_address"])
            print(json.dumps({
                "ok": ok,
                "wallet_address": creds["wallet_address"],
                "signature": signature,
                "payload": payload,
            }, indent=2, ensure_ascii=False))
        except Exception as e:
            log_event("ERROR", f"[CLOB] sign check failed: {e}", token_id=token_id, side=side)
            print(f"CLOB sign-check error: {e}")
    elif cmd == "clob-order-status":
        if len(sys.argv) < 3:
            print("Usage: python bot_v2.py clob-order-status <order_id> [--wait --timeout=60 --poll=3]")
            sys.exit(1)
        order_id = sys.argv[2]
        should_wait = "--wait" in sys.argv[3:]
        timeout_sec = 60
        poll_sec = 3
        for arg in sys.argv[3:]:
            if arg.startswith("--timeout="):
                timeout_sec = int(arg.split("=", 1)[1])
            elif arg.startswith("--poll="):
                poll_sec = int(arg.split("=", 1)[1])
        try:
            if should_wait:
                result = wait_for_order_fill(order_id, timeout_sec=timeout_sec, poll_interval=poll_sec)
            else:
                result = {"done": False, "status": fetch_order_status(order_id)}
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception as e:
            log_event("ERROR", f"[CLOB] order status failed: {e}", order_id=order_id)
            print(f"CLOB status error: {e}")
    else:
        print("Usage: python bot_v2.py [run|status|report|dashboard|clob-book|wallet-status|clob-order|clob-order-status|clob-sign-check]")
