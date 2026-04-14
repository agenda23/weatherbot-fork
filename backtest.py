#!/usr/bin/env python3
"""
backtest.py — Backtesting on collected market data

Replays resolved markets in data/markets/ using configurable strategy
parameters to evaluate performance without running the live bot.

Usage:
    python backtest.py                                         # defaults from config.json
    python backtest.py --param min_ev=0.15 max_price=0.40     # override params
    python backtest.py --sweep min_ev 0.05 0.10 0.15 0.20     # parameter sweep
    python backtest.py --sweep min_ev                          # sweep with built-in range
    python backtest.py --city chicago nyc                      # filter by city
    python backtest.py --sigma 1.5                             # override sigma
    python backtest.py --use-calibration                       # use data/calibration.json
    python backtest.py --verbose                               # print each trade
"""

import json
import math
import argparse
from pathlib import Path

DATA_DIR    = Path("data")
MARKETS_DIR = DATA_DIR / "markets"

with open("config.json", encoding="utf-8") as f:
    _base_cfg = json.load(f)

# =============================================================================
# PURE MATH  (mirrors weatherbet.py — kept local to avoid module-level side effects)
# =============================================================================

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def _in_bucket(forecast, t_low, t_high):
    if t_low == t_high:
        return round(float(forecast)) == round(t_low)
    return t_low <= float(forecast) <= t_high

def bucket_prob(forecast, t_low, t_high, sigma=2.0):
    if t_low == -999:
        return norm_cdf((t_high - float(forecast)) / sigma)
    if t_high == 999:
        return 1.0 - norm_cdf((t_low - float(forecast)) / sigma)
    p_high = norm_cdf((t_high - float(forecast)) / sigma)
    p_low  = norm_cdf((t_low  - float(forecast)) / sigma)
    return p_high - p_low

def calc_ev(p, price):
    if price <= 0 or price >= 1:
        return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)

def calc_kelly(p, price, kelly_fraction=0.25):
    if price <= 0 or price >= 1:
        return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * kelly_fraction, 1.0), 4)

# =============================================================================
# CONFIG
# =============================================================================

# Default sweep ranges per parameter
SWEEP_DEFAULTS = {
    "min_ev":          [0.05, 0.08, 0.10, 0.12, 0.15, 0.20],
    "max_price":       [0.30, 0.35, 0.40, 0.45, 0.50],
    "kelly_fraction":  [0.10, 0.15, 0.20, 0.25, 0.30],
    "sigma_f":         [1.0, 1.5, 2.0, 2.5, 3.0],
    "sigma_c":         [0.5, 0.8, 1.0, 1.2, 1.5, 2.0],
    "max_slippage":    [0.02, 0.03, 0.04, 0.05],
    "min_volume":      [200, 500, 1000, 2000],
}

class Config:
    def __init__(self, **overrides):
        c = {**_base_cfg, **overrides}
        self.balance        = float(c.get("balance", 10000.0))
        self.max_bet        = float(c.get("max_bet", 20.0))
        self.min_ev         = float(c.get("min_ev", 0.10))
        self.max_price      = float(c.get("max_price", 0.45))
        self.min_volume     = float(c.get("min_volume", 500))
        self.kelly_fraction = float(c.get("kelly_fraction", 0.25))
        self.max_slippage   = float(c.get("max_slippage", 0.03))
        self.sigma_f        = float(c.get("sigma_f", 2.0))
        self.sigma_c        = float(c.get("sigma_c", 1.2))

    def label(self):
        return (
            f"min_ev={self.min_ev}  max_price={self.max_price}  "
            f"kelly={self.kelly_fraction}  σ_F={self.sigma_f}  σ_C={self.sigma_c}"
        )

# =============================================================================
# DATA LOADING
# =============================================================================

def load_markets(city_filter=None):
    markets = []
    if not MARKETS_DIR.exists():
        return markets
    for f in MARKETS_DIR.glob("*.json"):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
            if city_filter and m.get("city") not in city_filter:
                continue
            markets.append(m)
        except Exception:
            pass
    return markets

def load_calibration():
    cal_file = DATA_DIR / "calibration.json"
    if cal_file.exists():
        return json.loads(cal_file.read_text(encoding="utf-8"))
    return {}

# =============================================================================
# BACKTESTING ENGINE
# =============================================================================

def _entry_snapshot(mkt, min_hours=2.0):
    """Return the first snapshot with enough hours remaining to resolution."""
    for snap in mkt.get("forecast_snapshots", []):
        if snap.get("hours_left", 0) >= min_hours:
            return snap
    return None

def run_backtest(markets, cfg, calibration=None, forward=False):
    """
    Replay markets with the given config.

    Modes
    -----
    forward=False (default — backtest):
        Candidates: markets where the bot actually entered and resolved_outcome
        is recorded.  Win/loss taken from resolved_outcome.
        Use-case: evaluate sizing/threshold changes on trades already made.

    forward=True (forward test):
        Candidates: ALL markets with actual_temp recorded (regardless of whether
        the bot entered).  Win/loss determined locally: actual_temp inside the
        would-have-entered bucket → WIN, outside → LOSS.
        Requires weatherbet.py to have fetched actual_temp via vc_key.
        Use-case: full parameter evaluation including markets the bot skipped.

    Entry decision point: first forecast snapshot with >= 2h left.
    Market prices used: all_outcomes stored in the market file.

    Returns (trades, skipped_counts).
    """
    if forward:
        candidates = [
            m for m in markets
            if m.get("actual_temp") is not None
            and m.get("status") in ("closed", "resolved")
        ]
        skipped = {
            "no_actual": 0, "no_snap": 0, "no_forecast": 0, "no_bucket": 0,
            "volume": 0, "price": 0, "slippage": 0, "ev": 0, "size": 0,
        }
    else:
        candidates = [
            m for m in markets
            if m.get("status") == "resolved"
            and m.get("resolved_outcome") in ("win", "loss")
        ]
        skipped = {
            "no_snap": 0, "no_forecast": 0, "no_bucket": 0,
            "volume": 0, "price": 0, "slippage": 0, "ev": 0, "size": 0,
        }

    trades  = []

    for mkt in candidates:
        city = mkt["city"]
        unit = mkt.get("unit", "F")

        # --- determine sigma ---
        entry_snap = _entry_snapshot(mkt)
        if not entry_snap:
            skipped["no_snap"] += 1
            continue

        best_source = entry_snap.get("best_source", "ecmwf")

        if calibration:
            # prefer blended sigma if available, else per-source
            sigma = (
                entry_snap.get("blended_sigma")
                or calibration.get(f"{city}_{best_source}", {}).get("sigma")
                or (cfg.sigma_f if unit == "F" else cfg.sigma_c)
            )
        else:
            sigma = cfg.sigma_f if unit == "F" else cfg.sigma_c

        # --- forecast temperature ---
        # prefer blended if stored, else best
        forecast_temp = entry_snap.get("blended") or entry_snap.get("best")
        if forecast_temp is None:
            skipped["no_forecast"] += 1
            continue

        # --- find matching bucket ---
        matched = None
        for o in mkt.get("all_outcomes", []):
            t_low, t_high = o["range"]
            if _in_bucket(forecast_temp, t_low, t_high):
                matched = o
                break

        if not matched:
            skipped["no_bucket"] += 1
            continue

        t_low, t_high = matched["range"]
        ask    = matched.get("ask", matched["price"])
        volume = matched.get("volume", 0)
        spread = matched.get("spread", 0)

        # --- filters ---
        if volume < cfg.min_volume:
            skipped["volume"] += 1
            continue
        if ask >= cfg.max_price:
            skipped["price"] += 1
            continue
        if spread > cfg.max_slippage:
            skipped["slippage"] += 1
            continue

        p  = bucket_prob(forecast_temp, t_low, t_high, sigma)
        ev = calc_ev(p, ask)
        if ev < cfg.min_ev:
            skipped["ev"] += 1
            continue

        kelly = calc_kelly(p, ask, cfg.kelly_fraction)
        size  = round(min(kelly * cfg.balance, cfg.max_bet), 2)
        if size < 0.50:
            skipped["size"] += 1
            continue

        # --- outcome ---
        actual_temp = mkt.get("actual_temp")
        if forward:
            # Local judgment: did actual temp land in the bucket we would enter?
            if actual_temp is None:
                skipped["no_actual"] += 1
                continue
            won = _in_bucket(actual_temp, t_low, t_high)
        else:
            won = mkt["resolved_outcome"] == "win"

        shares = size / ask
        pnl    = round(shares * (1.0 - ask), 2) if won else round(-size, 2)

        trades.append({
            "city":       city,
            "date":       mkt["date"],
            "unit":       unit,
            "forecast":   forecast_temp,
            "actual":     actual_temp,
            "bucket":     f"{t_low}-{t_high}",
            "price":      round(ask, 4),
            "p":          round(p, 4),
            "ev":         round(ev, 4),
            "sigma":      round(sigma, 3),
            "size":       size,
            "won":        won,
            "pnl":        pnl,
            "hours_left": entry_snap.get("hours_left"),
            "source":     best_source,
        })

    return trades, skipped

# =============================================================================
# METRICS
# =============================================================================

def compute_metrics(trades, cfg):
    if not trades:
        return {}

    pnls      = [t["pnl"] for t in trades]
    total_pnl = sum(pnls)
    wins      = [t for t in trades if t["won"]]

    # Running balance → max drawdown
    balance = cfg.balance
    peak    = balance
    max_dd  = 0.0
    for p in pnls:
        balance += p
        peak = max(peak, balance)
        dd = (peak - balance) / peak
        max_dd = max(max_dd, dd)

    # Sharpe (trade-level, not annualised)
    sharpe = 0.0
    if len(pnls) > 1:
        mean_p = total_pnl / len(pnls)
        var_p  = sum((p - mean_p) ** 2 for p in pnls) / (len(pnls) - 1)
        std_p  = math.sqrt(var_p)
        sharpe = round(mean_p / std_p, 3) if std_p > 0 else 0.0

    # Expectancy per trade
    expectancy = round(total_pnl / len(trades), 4) if trades else 0.0

    # By-city breakdown
    by_city = {}
    for t in trades:
        c = t["city"]
        if c not in by_city:
            by_city[c] = {"trades": 0, "wins": 0, "pnl": 0.0}
        by_city[c]["trades"] += 1
        by_city[c]["wins"]   += 1 if t["won"] else 0
        by_city[c]["pnl"]    = round(by_city[c]["pnl"] + t["pnl"], 2)

    return {
        "n_trades":      len(trades),
        "n_wins":        len(wins),
        "win_rate":      round(len(wins) / len(trades), 3),
        "total_pnl":     round(total_pnl, 2),
        "roi":           round(total_pnl / cfg.balance * 100, 2),
        "max_drawdown":  round(max_dd * 100, 2),
        "sharpe":        sharpe,
        "expectancy":    expectancy,
        "final_balance": round(cfg.balance + total_pnl, 2),
        "by_city":       by_city,
    }

# =============================================================================
# OUTPUT
# =============================================================================

def print_result(cfg, trades, metrics, skipped=None, verbose=False, forward=False):
    mode_tag = "FORWARD TEST" if forward else "BACKTEST"
    print(f"\n{'='*65}")
    print(f"  [{mode_tag}]  {cfg.label()}")
    print(f"{'='*65}")

    if not trades:
        print("  No qualifying trades found.")
        if skipped:
            _print_skipped(skipped)
        return

    sign = "+" if metrics["total_pnl"] >= 0 else ""
    print(f"  Trades:      {metrics['n_trades']} | Wins: {metrics['n_wins']} | WR: {metrics['win_rate']:.0%}")
    print(f"  PnL:         {sign}${metrics['total_pnl']:.2f}  (ROI: {sign}{metrics['roi']:.1f}%)")
    print(f"  Final bal:   ${metrics['final_balance']:,.2f}")
    print(f"  Expectancy:  ${metrics['expectancy']:+.4f} / trade")
    print(f"  Max drawdown:{metrics['max_drawdown']:.1f}%")
    print(f"  Sharpe:      {metrics['sharpe']:.3f}")

    if metrics["by_city"]:
        print(f"\n  By city:")
        rows = sorted(metrics["by_city"].items(), key=lambda x: -x[1]["pnl"])
        for city, s in rows:
            wr  = s["wins"] / s["trades"] if s["trades"] else 0
            pnl = s["pnl"]
            print(f"    {city:<16} {s['wins']}/{s['trades']} ({wr:.0%})  PnL: {'+'if pnl>=0 else ''}{pnl:.2f}")

    if skipped:
        _print_skipped(skipped)

    if verbose and trades:
        print(f"\n  Trade log:")
        for t in sorted(trades, key=lambda x: x["date"]):
            result = "WIN " if t["won"] else "LOSS"
            fc     = f"{t['forecast']}{t['unit']}"
            ac     = f"→{t['actual']}{t['unit']}" if t["actual"] is not None else ""
            print(
                f"    {t['city']:<14} {t['date']}  {t['bucket']:<14} "
                f"fc={fc}{ac}  σ={t['sigma']:.1f}  p={t['p']:.2f}  "
                f"ev={t['ev']:+.3f}  ${t['price']:.3f}  "
                f"{result} {'+'if t['pnl']>=0 else ''}{t['pnl']:.2f}"
            )

    print()

def _print_skipped(skipped):
    total = sum(skipped.values())
    if not total:
        return
    parts = [f"{k}={v}" for k, v in skipped.items() if v]
    print(f"\n  Skipped ({total}): {',  '.join(parts)}")

def print_sweep_table(param_name, results, forward=False):
    mode_tag = "FORWARD TEST" if forward else "BACKTEST"
    print(f"\n[{mode_tag}] Parameter sweep: {param_name}")
    print(f"  {'Value':>8}  {'Trades':>7}  {'WR':>6}  {'PnL':>10}  {'ROI':>7}  {'MaxDD':>7}  {'Sharpe':>7}  {'Expect':>9}")
    print(f"  {'-'*8}  {'-'*7}  {'-'*6}  {'-'*10}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*9}")
    for value, m in results:
        if not m:
            print(f"  {value:>8.3f}  {'—':>7}  {'—':>6}  {'—':>10}  {'—':>7}  {'—':>7}  {'—':>7}  {'—':>9}")
            continue
        sign = "+" if m["total_pnl"] >= 0 else ""
        print(
            f"  {value:>8.3f}  {m['n_trades']:>7}  {m['win_rate']:>5.0%}  "
            f"  {sign}{m['total_pnl']:>9.2f}  {sign}{m['roi']:>5.1f}%  "
            f"  {m['max_drawdown']:>5.1f}%  {m['sharpe']:>7.3f}  "
            f"  {m['expectancy']:>+8.4f}"
        )
    print()

# =============================================================================
# CLI
# =============================================================================

def parse_overrides(param_list):
    overrides = {}
    if not param_list:
        return overrides
    for kv in param_list:
        k, _, v = kv.partition("=")
        try:
            overrides[k.strip()] = float(v.strip())
        except ValueError:
            overrides[k.strip()] = v.strip()
    return overrides

def main():
    parser = argparse.ArgumentParser(
        description="WeatherBet backtest — replay resolved markets with configurable params"
    )
    parser.add_argument(
        "--param", nargs="*", metavar="KEY=VALUE",
        help="Override config params (e.g. min_ev=0.15 max_price=0.40)",
    )
    parser.add_argument(
        "--sweep", nargs="+", metavar="PARAM [VALUES...]",
        help="Sweep a parameter (e.g. --sweep min_ev  or  --sweep min_ev 0.05 0.10 0.20)",
    )
    parser.add_argument(
        "--city", nargs="+", metavar="CITY",
        help="Filter by city slug(s) (e.g. --city chicago nyc)",
    )
    parser.add_argument(
        "--sigma", type=float, default=None,
        help="Override default sigma for all cities",
    )
    parser.add_argument(
        "--use-calibration", action="store_true",
        help="Load sigma values from data/calibration.json",
    )
    parser.add_argument(
        "--forward", action="store_true",
        help=(
            "Forward-test mode: evaluate ALL markets with actual_temp recorded, "
            "including ones the bot skipped. Win/loss judged locally from actual_temp "
            "vs the bucket that would have been entered. Requires vc_key to be set."
        ),
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print individual trade details",
    )
    args = parser.parse_args()

    overrides = parse_overrides(args.param)
    if args.sigma is not None:
        overrides["sigma_f"] = args.sigma
        overrides["sigma_c"] = args.sigma

    # Load market data
    markets = load_markets(city_filter=args.city)
    total   = len(markets)

    if args.forward:
        eligible = [m for m in markets if m.get("actual_temp") is not None]
        print(f"\nLoaded {total} markets ({len(eligible)} with actual_temp) from {MARKETS_DIR}")
        if not eligible:
            print(
                "  No markets with actual_temp found.\n"
                "  Ensure vc_key is set in config.json and weatherbet.py has run long enough\n"
                "  for markets to close and actual temps to be fetched."
            )
            return
    else:
        resolved = [m for m in markets if m.get("status") == "resolved"]
        print(f"\nLoaded {total} markets ({len(resolved)} resolved) from {MARKETS_DIR}")
        if not resolved:
            print(
                "  No resolved markets found.\n"
                "  Run weatherbet.py for a while so markets accumulate in data/markets/."
            )
            return

    if args.city:
        print(f"  City filter: {', '.join(args.city)}")

    mode_note = "forward-test (actual_temp judgment)" if args.forward else "backtest (resolved_outcome)"
    print(f"  Mode: {mode_note}")

    # Calibration
    calibration = None
    if args.use_calibration:
        calibration = load_calibration()
        if calibration:
            print(f"  Calibration: {len(calibration)} city/source sigma values loaded")
        else:
            print("  Calibration: no data/calibration.json found — using default sigma")

    if args.sweep:
        param_name = args.sweep[0]
        if len(args.sweep) > 1:
            values = [float(v) for v in args.sweep[1:]]
        else:
            values = SWEEP_DEFAULTS.get(param_name)
            if values is None:
                print(f"  No default sweep range for '{param_name}'. Provide values explicitly.")
                return

        results = []
        for v in values:
            cfg       = Config(**{**overrides, param_name: v})
            trades, _ = run_backtest(markets, cfg, calibration, forward=args.forward)
            results.append((v, compute_metrics(trades, cfg)))

        print_sweep_table(param_name, results, forward=args.forward)

    else:
        cfg = Config(**overrides)
        trades, skipped = run_backtest(markets, cfg, calibration, forward=args.forward)
        metrics = compute_metrics(trades, cfg)
        print_result(cfg, trades, metrics, skipped=skipped, verbose=args.verbose, forward=args.forward)

if __name__ == "__main__":
    main()
