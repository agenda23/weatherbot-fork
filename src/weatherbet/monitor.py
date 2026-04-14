"""monitor.py — Quick stop/take-profit monitoring between hourly scans."""

import requests
from datetime import datetime, timezone

from weatherbet.config import LOCATIONS
from weatherbet.notify import log_event, send_discord_notification, track_api_result
from weatherbet.storage.state import load_state, save_state
from weatherbet.storage.markets import load_all_markets, save_market
from weatherbet.market.parser import hours_to_resolution
from weatherbet.strategy.risk import calc_dynamic_stop_price, calc_take_profit_threshold
from weatherbet.report import export_dashboard_data


def monitor_positions():
    """Quick stop check on open positions without full scan."""
    markets  = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    if not open_pos:
        try:
            export_dashboard_data()
        except Exception:
            pass
        return 0

    state   = load_state()
    balance = state["balance"]
    closed  = 0

    for mkt in open_pos:
        pos = mkt["position"]
        mid = pos["market_id"]

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

        end_date   = mkt.get("event_end_date", "")
        hours_left = hours_to_resolution(end_date) if end_date else 999.0
        take_profit = calc_take_profit_threshold(hours_left)

        if current_price >= entry * 1.20 and stop < entry:
            pos["stop_price"] = entry
            pos["trailing_activated"] = True
            print(f"  [TRAILING] {city_name} {mkt['date']} — stop moved to breakeven ${entry:.3f}")
            log_event(
                "INFO",
                f"[TRAILING] {city_name} {mkt['date']} — stop moved to breakeven ${entry:.3f}",
                city=mkt["city"], date=mkt["date"], stop_price=entry,
            )

        take_triggered = take_profit is not None and current_price >= take_profit
        stop_triggered = current_price <= stop

        if take_triggered or stop_triggered:
            pnl = round((current_price - entry) * pos["shares"], 2)
            balance += pos["cost"] + pnl
            pos["closed_at"] = datetime.now(timezone.utc).isoformat()
            if take_triggered:
                pos["close_reason"] = "take_profit"
                reason = "TAKE"
            elif current_price < entry:
                pos["close_reason"] = "stop_loss"
                reason = "STOP"
            else:
                pos["close_reason"] = "trailing_stop"
                reason = "TRAILING BE"
            pos["exit_price"] = current_price
            pos["pnl"]        = pnl
            pos["status"]     = "closed"
            closed += 1
            print(
                f"  [{reason}] {city_name} {mkt['date']} | entry ${entry:.3f} exit ${current_price:.3f} | "
                f"{hours_left:.0f}h left | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}"
            )
            log_event(
                "WARNING" if pos["close_reason"] == "stop_loss" else "INFO",
                f"[{reason}] {city_name} {mkt['date']} | entry ${entry:.3f} exit ${current_price:.3f} | "
                f"{hours_left:.0f}h left | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}",
                city=mkt["city"], date=mkt["date"], reason=pos["close_reason"], pnl=pnl,
            )
            if pos["close_reason"] == "stop_loss":
                send_discord_notification(
                    f"STOP LOSS: {city_name} {mkt['date']} | entry ${entry:.3f} -> exit ${current_price:.3f} | PnL {pnl:+.2f}"
                )
            save_market(mkt)

    if closed:
        state["balance"] = round(balance, 2)
        save_state(state)

    try:
        export_dashboard_data()
    except Exception as e:
        log_event("WARNING", f"[DASHBOARD] export failed: {e}")

    return closed
