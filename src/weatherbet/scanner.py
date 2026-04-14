"""scanner.py — Main hourly scan: forecast, entry, stop, auto-resolution."""

import json
import time
import requests
from datetime import datetime, timezone, timedelta

from weatherbet.config import (
    LOCATIONS, MONTHS,
    MIN_EV, MAX_PRICE, MIN_VOLUME, MIN_HOURS, MAX_HOURS, MAX_SLIPPAGE,
    CALIBRATION_MIN, DAILY_LOSS_LIMIT_PCT,
)
from weatherbet import calibration as cal_mod
from weatherbet.notify import log_event, send_discord_notification, track_api_result
from weatherbet.storage.state import load_state, save_state
from weatherbet.storage.markets import load_market, save_market, load_all_markets, new_market
from weatherbet.market.parser import parse_temp_range, hours_to_resolution, in_bucket
from weatherbet.market.polymarket import get_polymarket_event, check_market_resolved
from weatherbet.forecast.blend import take_forecast_snapshot
from weatherbet.strategy.probability import bucket_prob
from weatherbet.strategy.kelly import calc_ev, calc_kelly, bet_size
from weatherbet.strategy.risk import (
    get_today_realized_loss,
    calc_dynamic_stop_price,
    calc_take_profit_threshold,
)


def scan_and_update():
    """Main function of one cycle: updates forecasts, opens/closes positions."""
    now     = datetime.now(timezone.utc)
    state   = load_state()
    balance = state["balance"]
    new_pos = 0
    closed  = 0
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
        return 0, 0, 0

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

            mkt = load_market(city_slug, date)
            if mkt is None:
                if hours < MIN_HOURS or hours > MAX_HOURS:
                    continue
                mkt = new_market(city_slug, date, event, hours)

            if mkt["status"] == "resolved":
                continue

            # Update outcomes list
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
                    "price":     round(bid, 4),
                    "spread":    round(ask - bid, 4),
                    "volume":    round(volume, 0),
                })

            outcomes.sort(key=lambda x: x["range"][0])
            mkt["all_outcomes"] = outcomes

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

            top = max(outcomes, key=lambda x: x["price"]) if outcomes else None
            mkt["market_snapshots"].append({
                "ts":         snap.get("ts"),
                "top_bucket": f"{top['range'][0]}-{top['range'][1]}{unit_sym}" if top else None,
                "top_price":  top["price"] if top else None,
            })

            forecast_temp = snap.get("best")
            best_source   = snap.get("best_source")

            # --- STOP-LOSS AND TRAILING STOP ---
            if mkt.get("position") and mkt["position"].get("status") == "open":
                pos = mkt["position"]
                current_price = None
                for o in outcomes:
                    if o["market_id"] == pos["market_id"]:
                        current_price = o.get("bid", o["price"])
                        break

                if current_price is not None:
                    entry = pos["entry_price"]
                    sigma = pos.get("sigma")
                    stop  = pos.get("stop_price", calc_dynamic_stop_price(entry, sigma, unit))

                    if current_price >= entry * 1.20 and stop < entry:
                        pos["stop_price"] = entry
                        pos["trailing_activated"] = True

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
                        log_event(
                            "WARNING" if current_price < entry else "INFO",
                            f"[{reason}] {loc['name']} {date} | entry ${entry:.3f} exit ${current_price:.3f} | PnL: {'+'if pnl>=0 else ''}{pnl:.2f}",
                            city=city_slug, date=date, reason=pos["close_reason"], pnl=pnl,
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
                            city=city_slug, date=date, reason="forecast_changed", pnl=pnl,
                        )

            # --- OPEN POSITION ---
            if not mkt.get("position") and forecast_temp is not None and hours >= MIN_HOURS:
                if (city_slug, date) in open_keys:
                    log_event(
                        "INFO",
                        f"[SKIP] Correlation guard: existing open position for {loc['name']} {date}",
                        city=city_slug, date=date, reason="correlation_guard",
                    )
                    continue
                sigma = cal_mod.get_sigma(city_slug, best_source or "ecmwf")
                best_signal = None

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
                                    "forecast_temp": forecast_temp,
                                    "forecast_src":  best_source,
                                    "sigma":         sigma,
                                    "opened_at":     snap.get("ts"),
                                    "status":        "open",
                                    "pnl":           None,
                                    "exit_price":    None,
                                    "close_reason":  None,
                                    "closed_at":     None,
                                }

                if best_signal:
                    skip_position = False
                    try:
                        r = requests.get(
                            f"https://gamma-api.polymarket.com/markets/{best_signal['market_id']}",
                            timeout=(3, 5),
                        )
                        mdata = r.json()
                        track_api_result("polymarket_market", True)
                        real_ask = float(mdata.get("bestAsk", best_signal["entry_price"]))
                        real_bid = float(mdata.get("bestBid", best_signal["bid_at_entry"]))
                        real_spread = round(real_ask - real_bid, 4)
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
                        print(
                            f"  [BUY]  {loc['name']} {horizon} {date} | {bucket_label} | "
                            f"${best_signal['entry_price']:.3f} | EV {best_signal['ev']:+.2f} | "
                            f"${best_signal['cost']:.2f} ({best_signal['forecast_src'].upper()})"
                        )
                        log_event(
                            "INFO",
                            f"[BUY] {loc['name']} {horizon} {date} | {bucket_label} | "
                            f"${best_signal['entry_price']:.3f} | EV {best_signal['ev']:+.2f} | "
                            f"${best_signal['cost']:.2f} ({best_signal['forecast_src'].upper()})",
                            city=city_slug, date=date, horizon=horizon,
                            market_id=best_signal["market_id"],
                            ev=best_signal["ev"], cost=best_signal["cost"],
                        )
                        send_discord_notification(
                            f"NEW POSITION: {loc['name']} {date} {bucket_label} | "
                            f"entry ${best_signal['entry_price']:.3f} | size ${best_signal['cost']:.2f} | "
                            f"EV {best_signal['ev']:+.2f}"
                        )

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

        won = check_market_resolved(market_id)
        if won is None:
            continue

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
            city=mkt["city"], date=mkt["date"], result=mkt["resolved_outcome"], pnl=pnl,
        )
        resolved += 1
        save_market(mkt)
        time.sleep(0.3)

    state["balance"]      = round(balance, 2)
    state["peak_balance"] = max(state.get("peak_balance", balance), balance)
    save_state(state)

    all_mkts = load_all_markets()
    resolved_count = len([m for m in all_mkts if m["status"] == "resolved"])
    if resolved_count >= CALIBRATION_MIN:
        cal_mod.run_calibration(all_mkts)

    return new_pos, closed, resolved
