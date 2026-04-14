"""cli.py — Main loop and CLI entry point."""

import sys
import json
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import requests

from weatherbet import calibration as cal_mod
from weatherbet.config import (
    BALANCE, MAX_BET, SCAN_INTERVAL, LOCATIONS,
    CLOB_SIGNING_MODE,
)
from weatherbet.notify import log_event
from weatherbet.storage.state import load_state, save_state
from weatherbet.scanner import scan_and_update
from weatherbet.monitor import monitor_positions
from weatherbet.report import print_status, print_report, export_dashboard_data
from weatherbet.clob import (
    PolymarketCLOBClient,
    submit_clob_order,
    fetch_order_status,
    wait_for_order_fill,
    build_clob_order_payload,
    sign_clob_order_payload,
    verify_eth_sign_payload_signature,
    load_wallet_credentials,
    wallet_status,
)

MONITOR_INTERVAL = 600  # monitor positions every 10 minutes


def run_loop():
    cal_mod.init_cal()

    print(f"\n{'='*55}")
    print(f"  WEATHERBET — STARTING")
    print(f"{'='*55}")
    print(f"  Cities:     {len(LOCATIONS)}")
    print(f"  Balance:    ${BALANCE:,.0f} | Max bet: ${MAX_BET}")
    print(f"  Scan:       {SCAN_INTERVAL//60} min | Monitor: {MONITOR_INTERVAL//60} min")
    print(f"  Sources:    ECMWF + HRRR(US) + METAR(D+0)")
    print(f"  Ctrl+C to stop\n")

    last_full_scan = 0

    while True:
        now_ts  = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if now_ts - last_full_scan >= SCAN_INTERVAL:
            print(f"[{now_str}] full scan...")
            try:
                new_pos, closed, resolved = scan_and_update()
                state = load_state()
                print(
                    f"  balance: ${state['balance']:,.2f} | "
                    f"new: {new_pos} | closed: {closed} | resolved: {resolved}"
                )
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


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"

    if cmd == "run":
        run_loop()

    elif cmd == "status":
        cal_mod.init_cal()
        print_status()

    elif cmd == "report":
        cal_mod.init_cal()
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
            print("Usage: python weatherbet.py clob-book <token_id>")
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
            print("Usage: python weatherbet.py clob-order <token_id> <buy|sell> <price> <size> [--live]")
            sys.exit(1)
        token_id = sys.argv[2]
        side  = sys.argv[3]
        price = sys.argv[4]
        size  = sys.argv[5]
        dry_run = "--live" not in sys.argv[6:]
        try:
            res = submit_clob_order(token_id, side, price, size, dry_run=dry_run)
            print(json.dumps(res, indent=2, ensure_ascii=False))
        except Exception as e:
            log_event("ERROR", f"[CLOB] order submit failed: {e}", token_id=token_id, side=side)
            print(f"CLOB order error: {e}")

    elif cmd == "clob-sign-check":
        if len(sys.argv) < 6:
            print("Usage: python weatherbet.py clob-sign-check <token_id> <buy|sell> <price> <size>")
            sys.exit(1)
        token_id = sys.argv[2]
        side  = sys.argv[3]
        price = sys.argv[4]
        size  = sys.argv[5]
        creds = load_wallet_credentials()
        try:
            payload   = build_clob_order_payload(token_id, side, price, size)
            signature = sign_clob_order_payload(payload, creds["private_key"], mode="eth_sign")
            ok = verify_eth_sign_payload_signature(payload, signature, creds["wallet_address"])
            print(json.dumps({
                "ok": ok,
                "wallet_address": creds["wallet_address"],
                "signature": signature,
                "payload":   payload,
            }, indent=2, ensure_ascii=False))
        except Exception as e:
            log_event("ERROR", f"[CLOB] sign check failed: {e}", token_id=token_id, side=side)
            print(f"CLOB sign-check error: {e}")

    elif cmd == "clob-order-status":
        if len(sys.argv) < 3:
            print("Usage: python weatherbet.py clob-order-status <order_id> [--wait --timeout=60 --poll=3]")
            sys.exit(1)
        order_id    = sys.argv[2]
        should_wait = "--wait" in sys.argv[3:]
        timeout_sec = 60
        poll_sec    = 3
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
        print(f"Unknown command: {cmd}")
        print("Usage: python weatherbet.py [run|status|report|dashboard|clob-book|wallet-status|clob-order|clob-sign-check|clob-order-status]")
        sys.exit(1)
