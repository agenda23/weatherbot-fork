"""clob.py — Polymarket CLOB REST client, wallet, and order signing."""

import re
import json
import time
import hashlib
import requests
from datetime import datetime, timezone

from weatherbet.config import (
    CLOB_BASE_URL,
    CLOB_API_KEY,
    POLYGON_WALLET_ADDRESS,
    POLYGON_PRIVATE_KEY,
    LIVE_TRADING_ENABLED,
    CLOB_SIGNING_MODE,
)
from weatherbet.notify import log_event


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
        return self.get_json("/book", params={"token_id": token_id})

    def place_order(self, payload):
        return self.post_json("/order", payload)

    def get_order_status(self, order_id):
        return self.get_json(f"/order/{order_id}")


def build_clob_order_payload(token_id, side, price, size):
    side_norm = side.lower().strip()
    if side_norm not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    return {
        "token_id":  str(token_id),
        "side":      side_norm,
        "price":     round(float(price), 6),
        "size":      round(float(size), 6),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def sign_clob_order_payload(payload, private_key, mode=None):
    """Signs an order payload.

    mode:
      - stub:     deterministic local stub signature
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
            "mode":         "dry_run",
            "live_enabled": LIVE_TRADING_ENABLED,
            "payload":      signed_payload,
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


def mask_secret(value, keep=4):
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
    return {
        "private_key":    private_key,
        "wallet_address": POLYGON_WALLET_ADDRESS,
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
    return {
        "has_private_key":    bool(private_key),
        "private_key_valid":  validate_private_key(private_key),
        "masked_private_key": mask_secret(private_key),
        "wallet_address":     creds["wallet_address"],
    }
