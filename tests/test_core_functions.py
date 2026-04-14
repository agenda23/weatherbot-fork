"""test_core_functions.py — Unit tests for weatherbet core modules."""

import math
from datetime import datetime, timezone

import pytest

import weatherbet.market.parser as parser_mod
import weatherbet.strategy.probability as prob_mod
import weatherbet.strategy.kelly as kelly_mod
import weatherbet.strategy.risk as risk_mod
import weatherbet.calibration as cal_mod
import weatherbet.forecast.blend as blend_mod
import weatherbet.notify as notify_mod
import weatherbet.storage.state as state_mod
import weatherbet.storage.markets as markets_mod
import weatherbet.report as report_mod
import weatherbet.clob as clob_mod

try:
    from eth_account import Account
    _HAS_ETH_ACCOUNT = True
except Exception:
    _HAS_ETH_ACCOUNT = False


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Will temperature be between 70-74F on April 14?", (70.0, 74.0)),
        ("Will temp be 72F on April 14?", (72.0, 72.0)),
        ("Will temp be 18C on April 14?", (18.0, 18.0)),
        ("Will temp be 60F or below?", (-999.0, 60.0)),
        ("Will temp be 22C or higher?", (22.0, 999.0)),
    ],
)
def test_parse_temp_range_patterns(question, expected):
    assert parser_mod.parse_temp_range(question) == expected


def test_parse_temp_range_invalid():
    assert parser_mod.parse_temp_range("random question") is None


# ---------------------------------------------------------------------------
# probability
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("forecast", "t_low", "t_high", "sigma", "expected"),
    [
        (60, -999, 62, 0, 1.0),
        (64, -999, 62, 0, 0.0),
        (64, 62, 999, 0, 1.0),
        (60, 62, 999, 0, 0.0),
        (70, 70, 70, 0, 1.0),
    ],
)
def test_bucket_prob_sigma_zero_edges(forecast, t_low, t_high, sigma, expected):
    assert prob_mod.bucket_prob(forecast, t_low, t_high, sigma=sigma) == expected


def test_bucket_prob_upper_and_lower_edge_monotonic():
    lower = prob_mod.bucket_prob(60, -999, 62, sigma=2.0)
    upper = prob_mod.bucket_prob(60, 62, 999, sigma=2.0)
    assert 0.0 <= lower <= 1.0
    assert 0.0 <= upper <= 1.0


# ---------------------------------------------------------------------------
# kelly
# ---------------------------------------------------------------------------

def test_calc_ev():
    assert kelly_mod.calc_ev(0.6, 0.4) == 0.5
    assert kelly_mod.calc_ev(0.5, 0.0) == 0.0
    assert kelly_mod.calc_ev(0.5, 1.0) == 0.0


def test_calc_kelly_range(monkeypatch):
    monkeypatch.setattr(kelly_mod, "KELLY_FRACTION", 0.25)
    kelly = kelly_mod.calc_kelly(0.6, 0.4)
    assert 0.0 <= kelly <= 1.0
    assert kelly == 0.0833


def test_bet_size_cap(monkeypatch):
    monkeypatch.setattr(kelly_mod, "MAX_BET", 20.0)
    assert kelly_mod.bet_size(0.5, 1000) == 20.0
    assert kelly_mod.bet_size(0.01, 1000) == 10.0


# ---------------------------------------------------------------------------
# parser — in_bucket
# ---------------------------------------------------------------------------

def test_in_bucket_single_degree_rounding():
    assert parser_mod.in_bucket(70.4, 70, 70) is True
    assert parser_mod.in_bucket(70.6, 70, 70) is False


def test_in_bucket_range():
    assert parser_mod.in_bucket(71, 70, 72) is True
    assert parser_mod.in_bucket(69.9, 70, 72) is False


# ---------------------------------------------------------------------------
# blend
# ---------------------------------------------------------------------------

def test_blend_forecast_single_source(monkeypatch):
    monkeypatch.setattr(blend_mod, "get_sigma", lambda city, source="ecmwf": 2.5)
    blended_temp, blended_sigma = blend_mod.blend_forecast(70, None, "nyc")
    assert blended_temp == 70
    assert blended_sigma == 2.5


def test_blend_forecast_inverse_variance(monkeypatch):
    def fake_sigma(city, source="ecmwf"):
        return 2.0 if source == "ecmwf" else 1.0

    monkeypatch.setattr(blend_mod, "get_sigma", fake_sigma)
    blended_temp, blended_sigma = blend_mod.blend_forecast(70, 72, "nyc")

    expected_temp = round((70 * (1 / 4) + 72 * (1 / 1)) / ((1 / 4) + (1 / 1)))
    expected_sigma = round(math.sqrt(1 / ((1 / 4) + (1 / 1))), 3)
    assert blended_temp == expected_temp
    assert blended_sigma == expected_sigma


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------

def test_run_calibration_uses_rmse(monkeypatch):
    monkeypatch.setattr(cal_mod, "CALIBRATION_MIN", 1)
    monkeypatch.setattr(cal_mod, "load_cal", lambda: {})

    captured = {}

    class DummyFile:
        def write_text(self, content, encoding="utf-8"):
            captured["content"] = content

    monkeypatch.setattr(cal_mod, "CALIBRATION_FILE", DummyFile())

    markets = [
        {
            "resolved": True,
            "city": "nyc",
            "actual_temp": 70.0,
            "forecast_snapshots": [{"source": "ecmwf", "temp": 68.0}],
        },
        {
            "resolved": True,
            "city": "nyc",
            "actual_temp": 70.0,
            "forecast_snapshots": [{"source": "ecmwf", "temp": 70.0}],
        },
        {
            "resolved": True,
            "city": "nyc",
            "actual_temp": 70.0,
            "forecast_snapshots": [{"source": "ecmwf", "temp": 72.0}],
        },
    ]

    cal = cal_mod.run_calibration(markets)
    sigma = cal["nyc_ecmwf"]["sigma"]

    expected_rmse = round(math.sqrt((2.0**2 + 0.0**2 + 2.0**2) / 3.0), 3)
    assert sigma == expected_rmse


# ---------------------------------------------------------------------------
# risk
# ---------------------------------------------------------------------------

def test_get_today_realized_loss():
    now = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    markets = [
        {"position": {"pnl": -12.5, "closed_at": "2026-04-14T03:10:00+00:00"}},
        {"position": {"pnl":   8.0, "closed_at": "2026-04-14T04:00:00+00:00"}},
        {"position": {"pnl":  -7.0, "closed_at": "2026-04-13T23:59:00+00:00"}},
    ]
    assert risk_mod.get_today_realized_loss(markets, now=now) == 12.5


def test_has_open_position_for_city_date():
    markets = [
        {"city": "nyc", "date": "2026-04-14", "position": {"status": "open"}},
        {"city": "nyc", "date": "2026-04-15", "position": {"status": "closed"}},
    ]
    assert risk_mod.has_open_position_for_city_date(markets, "nyc", "2026-04-14") is True
    assert risk_mod.has_open_position_for_city_date(markets, "nyc", "2026-04-15") is False


def test_calc_take_profit_threshold():
    assert risk_mod.calc_take_profit_threshold(12) is None
    assert risk_mod.calc_take_profit_threshold(24) == 0.85
    assert risk_mod.calc_take_profit_threshold(36) == 0.8
    assert risk_mod.calc_take_profit_threshold(48) == 0.75
    assert risk_mod.calc_take_profit_threshold(72) == 0.75


def test_calc_dynamic_stop_price_sigma_scaled():
    assert risk_mod.calc_dynamic_stop_price(1.0, 2.0, "F") == 0.8
    assert risk_mod.calc_dynamic_stop_price(1.0, 3.0, "F") == 0.7
    assert risk_mod.calc_dynamic_stop_price(1.0, 0.2, "F") == 0.9


# ---------------------------------------------------------------------------
# notify
# ---------------------------------------------------------------------------

def test_send_discord_notification_without_webhook(monkeypatch):
    monkeypatch.setattr(notify_mod, "DISCORD_WEBHOOK_URL", "")
    assert notify_mod.send_discord_notification("hello") is False


def test_send_discord_notification_success(monkeypatch):
    monkeypatch.setattr(notify_mod, "DISCORD_WEBHOOK_URL", "https://example.com/webhook")

    class DummyResponse:
        status_code = 204

    called = {}

    def fake_post(url, json=None, timeout=None):
        called["url"]     = url
        called["json"]    = json
        called["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(notify_mod.requests, "post", fake_post)
    ok = notify_mod.send_discord_notification("test message")
    assert ok is True
    assert called["url"]  == "https://example.com/webhook"
    assert called["json"] == {"content": "test message"}


def test_log_event_writes_json_line(monkeypatch, tmp_path):
    import json
    log_file = tmp_path / "weatherbet.log"
    monkeypatch.setattr(notify_mod, "LOG_FILE", log_file)
    notify_mod.log_event("info", "sample", city="nyc")
    line = log_file.read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["level"]   == "INFO"
    assert payload["message"] == "sample"
    assert payload["city"]    == "nyc"


def test_track_api_result_alert_threshold(monkeypatch):
    monkeypatch.setattr(notify_mod, "API_FAILURE_ALERT_THRESHOLD", 3)
    notify_mod.API_FAILURE_COUNTS.clear()
    sent = []

    monkeypatch.setattr(notify_mod, "send_discord_notification", lambda msg: sent.append(msg) or True)
    monkeypatch.setattr(notify_mod, "log_event", lambda *a, **kw: None)

    notify_mod.track_api_result("x_api", False, "e1")
    notify_mod.track_api_result("x_api", False, "e2")
    assert sent == []
    notify_mod.track_api_result("x_api", False, "e3")
    assert len(sent) == 1
    assert "x_api failed 3 times consecutively" in sent[0]


def test_track_api_result_resets_on_success(monkeypatch):
    monkeypatch.setattr(notify_mod, "API_FAILURE_ALERT_THRESHOLD", 2)
    notify_mod.API_FAILURE_COUNTS.clear()
    monkeypatch.setattr(notify_mod, "send_discord_notification", lambda msg: True)
    monkeypatch.setattr(notify_mod, "log_event", lambda *a, **kw: None)

    notify_mod.track_api_result("y_api", False, "e1")
    assert notify_mod.API_FAILURE_COUNTS["y_api"] == 1
    notify_mod.track_api_result("y_api", True)
    assert notify_mod.API_FAILURE_COUNTS["y_api"] == 0


# ---------------------------------------------------------------------------
# report / dashboard
# ---------------------------------------------------------------------------

def test_export_dashboard_data(monkeypatch, tmp_path):
    monkeypatch.setattr(
        report_mod, "load_state",
        lambda: {"balance": 1000.0, "starting_balance": 1000.0, "wins": 1, "losses": 1},
    )
    monkeypatch.setattr(
        report_mod, "load_all_markets",
        lambda: [
            {
                "city": "nyc", "city_name": "New York City", "date": "2026-04-14",
                "status": "open",
                "position": {
                    "status": "open", "market_id": "m1",
                    "entry_price": 0.4, "shares": 10, "cost": 4.0,
                    "bucket_low": 70, "bucket_high": 74, "forecast_src": "ecmwf",
                },
                "all_outcomes": [{"market_id": "m1", "bid": 0.5}],
            },
            {
                "city": "nyc", "city_name": "New York City", "date": "2026-04-13",
                "status": "resolved", "pnl": 2.5, "resolved_outcome": "win",
            },
        ],
    )
    import json
    monkeypatch.setattr(report_mod, "DASHBOARD_FILE", tmp_path / "dashboard.json")
    monkeypatch.setattr(report_mod, "log_event", lambda *a, **kw: None)

    output_path = report_mod.export_dashboard_data()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["summary"]["open_count"]         == 1
    assert payload["summary"]["resolved_count"]     == 1
    assert payload["summary"]["total_realized_pnl"] == 2.5
    assert len(payload["open_positions"])            == 1


# ---------------------------------------------------------------------------
# clob
# ---------------------------------------------------------------------------

def test_clob_client_headers_with_api_key():
    client = clob_mod.PolymarketCLOBClient(base_url="https://example.com", api_key="abc")
    headers = client._headers()
    assert headers["Accept"]        == "application/json"
    assert headers["Authorization"] == "Bearer abc"


def test_clob_client_get_orderbook(monkeypatch):
    client = clob_mod.PolymarketCLOBClient(base_url="https://example.com", api_key="")

    class DummyResponse:
        def raise_for_status(self): return None
        def json(self): return {"bids": [{"price": "0.4"}], "asks": [{"price": "0.5"}]}

    def fake_get(url, headers=None, params=None, timeout=None):
        assert url == "https://example.com/book"
        assert params == {"token_id": "token-1"}
        return DummyResponse()

    monkeypatch.setattr(clob_mod.requests, "get", fake_get)
    book = client.get_orderbook("token-1")
    assert book["bids"][0]["price"] == "0.4"


def test_validate_private_key():
    assert clob_mod.validate_private_key("0x" + "a" * 64) is True
    assert clob_mod.validate_private_key("b" * 64)         is True
    assert clob_mod.validate_private_key("0x1234")         is False
    assert clob_mod.validate_private_key("")                is False


def test_mask_secret():
    assert clob_mod.mask_secret("abcdefghijklmnop") == "abcd...mnop"
    assert clob_mod.mask_secret("abcd")             == "****"


def test_wallet_status(monkeypatch):
    monkeypatch.setattr(clob_mod, "POLYGON_PRIVATE_KEY",    "0x" + "c" * 64)
    monkeypatch.setattr(clob_mod, "POLYGON_WALLET_ADDRESS", "0x123")
    monkeypatch.setattr(clob_mod, "load_wallet_credentials", lambda: {
        "private_key":    "0x" + "c" * 64,
        "wallet_address": "0x123",
    })
    status = clob_mod.wallet_status()
    assert status["has_private_key"]   is True
    assert status["private_key_valid"] is True
    assert status["wallet_address"]    == "0x123"


def test_build_clob_order_payload():
    payload = clob_mod.build_clob_order_payload("t1", "buy", 0.42, 10)
    assert payload["token_id"] == "t1"
    assert payload["side"]     == "buy"
    assert payload["price"]    == 0.42
    assert payload["size"]     == 10.0


def test_sign_clob_order_payload_invalid_key():
    with pytest.raises(ValueError):
        clob_mod.sign_clob_order_payload({"a": 1}, "invalid")


def test_sign_clob_order_payload_stub():
    sig = clob_mod.sign_clob_order_payload({"a": 1}, "0x" + "a" * 64, mode="stub")
    assert sig.startswith("stub_")


def test_sign_clob_order_payload_unsupported_mode():
    with pytest.raises(ValueError):
        clob_mod.sign_clob_order_payload({"a": 1}, "0x" + "a" * 64, mode="unknown")


def test_sign_clob_order_payload_eth_sign_without_dependency():
    if _HAS_ETH_ACCOUNT:
        sig = clob_mod.sign_clob_order_payload({"a": 1}, "0x" + "a" * 64, mode="eth_sign")
        assert isinstance(sig, str) and len(sig) > 10
    else:
        with pytest.raises(RuntimeError):
            clob_mod.sign_clob_order_payload({"a": 1}, "0x" + "a" * 64, mode="eth_sign")


def test_verify_eth_sign_payload_signature_without_dependency():
    if _HAS_ETH_ACCOUNT:
        private_key = "0x" + "b" * 64
        payload = {"a": 1}
        signature = clob_mod.sign_clob_order_payload(payload, private_key, mode="eth_sign")
        expected_address = Account.from_key(private_key).address
        assert clob_mod.verify_eth_sign_payload_signature(payload, signature, expected_address) is True
    else:
        with pytest.raises(RuntimeError):
            clob_mod.verify_eth_sign_payload_signature({"a": 1}, "0x00", "0xabc")


def test_submit_clob_order_dry_run(monkeypatch):
    monkeypatch.setattr(clob_mod, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setattr(clob_mod, "load_wallet_credentials", lambda: {
        "private_key":    "0x" + "a" * 64,
        "wallet_address": "0xabc",
    })
    res = clob_mod.submit_clob_order("tok", "buy", 0.5, 2, dry_run=True)
    assert res["mode"] == "dry_run"
    assert res["payload"]["token_id"]       == "tok"
    assert res["payload"]["wallet_address"] == "0xabc"
    assert str(res["payload"]["signature"]).startswith("stub_")


def test_wait_for_order_fill_done(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(order_id):
        calls["n"] += 1
        if calls["n"] < 2:
            return {"status": "open"}
        return {"status": "filled", "order_id": order_id}

    monkeypatch.setattr(clob_mod, "fetch_order_status", fake_fetch)
    monkeypatch.setattr(clob_mod.time, "sleep", lambda _: None)
    res = clob_mod.wait_for_order_fill("o1", timeout_sec=5, poll_interval=1)
    assert res["done"] is True
    assert res["status"]["status"] == "filled"


def test_wait_for_order_fill_timeout(monkeypatch):
    monkeypatch.setattr(clob_mod, "fetch_order_status", lambda order_id: {"status": "open", "id": order_id})
    monkeypatch.setattr(clob_mod.time, "sleep", lambda _: None)
    timeline = {"t": 0}

    def fake_time():
        timeline["t"] += 2
        return timeline["t"]

    monkeypatch.setattr(clob_mod.time, "time", fake_time)
    res = clob_mod.wait_for_order_fill("o2", timeout_sec=3, poll_interval=1)
    assert res["done"] is False
    assert res["status"]["status"] == "open"
