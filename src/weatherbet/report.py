"""report.py — Status, full report, and dashboard export."""

import json
from datetime import datetime, timezone

from weatherbet.config import LOCATIONS, DASHBOARD_FILE
from weatherbet.notify import log_event
from weatherbet.storage.state import load_state
from weatherbet.storage.markets import load_all_markets


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
    if total:
        print(f"  Trades:      {total} | W: {wins} | L: {losses} | WR: {wins/total:.0%}")
    else:
        print(f"  No trades yet")
    print(f"  Open:        {len(open_pos)}")
    print(f"  Resolved:    {len(resolved)}")

    if open_pos:
        print(f"\n  Open positions:")
        total_unrealized = 0.0
        for m in open_pos:
            pos      = m["position"]
            unit_sym = "F" if m["unit"] == "F" else "C"
            label    = f"{pos['bucket_low']}-{pos['bucket_high']}{unit_sym}"
            current_price = pos["entry_price"]
            for o in m.get("all_outcomes", []):
                if o["market_id"] == pos["market_id"]:
                    current_price = o["price"]
                    break
            unrealized = round((current_price - pos["entry_price"]) * pos["shares"], 2)
            total_unrealized += unrealized
            pnl_str = f"{'+'if unrealized>=0 else ''}{unrealized:.2f}"
            print(
                f"    {m['city_name']:<16} {m['date']} | {label:<14} | "
                f"entry ${pos['entry_price']:.3f} -> ${current_price:.3f} | "
                f"PnL: {pnl_str} | {pos['forecast_src'].upper()}"
            )
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
    state   = load_state()
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
        open_positions.append({
            "city":             m.get("city"),
            "city_name":        m.get("city_name"),
            "date":             m.get("date"),
            "bucket_low":       pos.get("bucket_low"),
            "bucket_high":      pos.get("bucket_high"),
            "entry_price":      pos.get("entry_price"),
            "current_price":    current_price,
            "shares":           pos.get("shares"),
            "cost":             pos.get("cost"),
            "unrealized_pnl":   unrealized,
            "forecast_source":  pos.get("forecast_src"),
        })

    total_pnl = round(sum(float(m.get("pnl", 0)) for m in resolved), 2)
    wins   = len([m for m in resolved if m.get("resolved_outcome") == "win"])
    losses = len([m for m in resolved if m.get("resolved_outcome") == "loss"])

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state":        state,
        "summary": {
            "open_count":          len(open_pos),
            "resolved_count":      len(resolved),
            "wins":                wins,
            "losses":              losses,
            "total_realized_pnl":  total_pnl,
        },
        "open_positions": open_positions,
        "recent_resolved": sorted(
            [
                {
                    "city":      m.get("city"),
                    "city_name": m.get("city_name"),
                    "date":      m.get("date"),
                    "pnl":       m.get("pnl"),
                    "result":    m.get("resolved_outcome"),
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
