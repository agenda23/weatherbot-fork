# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bots

```bash
# weatherbet.py (full bot — use this for development)
python weatherbet.py           # main scan loop (hourly)
python weatherbet.py status    # balance and open positions
python weatherbet.py report    # full resolved-market breakdown

# weatherbet_v1.py (simple baseline, US-only)
python weatherbet_v1.py           # paper scan
python weatherbet_v1.py --live    # simulate trades against virtual balance
python weatherbet_v1.py --positions
python weatherbet_v1.py --reset
```

**Install deps**: `pip install -r requirements.txt` (core: `requests`; dev: `pytest`)

## Architecture

### Two independent bots

| File | Cities | Forecast sources | Risk model | State file |
|------|--------|-----------------|------------|-----------|
| `weatherbet_v1.py` | 6 US | NWS gridpoint + METAR obs | Flat 5% position size | `simulation.json` (root) |
| `weatherbet.py` | 20 (US/EU/Asia/CA/SA/OC) | ECMWF + HRRR (Open-Meteo) + METAR | Kelly criterion + EV filter + stops | `data/state.json`, `data/markets/`, `data/calibration.json` |

`weatherbet.py` is a thin entry point — the implementation lives in `src/weatherbet/`.

### Core data flow (weatherbet.py)

1. **Forecast fetch** — `get_ecmwf` / `get_hrrr` (Open-Meteo, no key) + `get_metar` (Aviation Weather). ECMWF covers all 20 cities (7-day). HRRR/GFS covers US-only (3-day).
2. **Ensemble blend** — `blend_forecast()` combines ECMWF + HRRR via inverse-variance weighting using per-source sigma. Produces `blended` temp and `blended_sigma` stored in each forecast snapshot.
3. **Probability estimation** — `bucket_prob()` uses normal CDF difference: `P = Φ((t_high - fc) / σ) - Φ((t_low - fc) / σ)` for all bucket types including edge buckets.
4. **Trade decision** — `calc_ev()` and `calc_kelly()` gate entries; filtered by `MIN_EV`, `MAX_PRICE`, `MIN_VOLUME`, `MAX_SLIPPAGE`, time-to-resolution window, and `MAX_OPEN_POS` portfolio cap.
5. **Market matching** — `get_polymarket_event()` builds the slug `highest-temperature-in-{city}-on-{month}-{day}-{year}` and hits Polymarket Gamma API. `parse_temp_range()` regex-parses the market question to find the matching bucket.
6. **Monitoring loop** — every 10 minutes checks open positions for stop-loss (80% of entry), trailing stop (moves to breakeven at +20%), time-dependent take-profit (≥$0.75 at 48h+, ≥$0.85 at 24–48h), and forecast drift (`forecast_changed`).
7. **Calibration** — after resolution, `run_calibration()` computes RMSE per city×source from `forecast_snapshots[n].best` vs `actual_temp`, updates sigma in `data/calibration.json`. Requires `calibration_min` (default 30) resolved samples.

### APIs used

| API | Auth | Purpose |
|-----|------|---------|
| Polymarket Gamma (`gamma-api.polymarket.com`) | None | Read-only: events, market prices, resolution status |
| Open-Meteo | None | ECMWF and HRRR forecast models |
| Aviation Weather (METAR) | None | Real-time station observations |
| Visual Crossing | Free key (`vc_key` in config) | Actual temperatures for post-resolution calibration; required for `actual_temp` recording and calibration |

**No real trading**: the bot only reads Polymarket — no wallet, no signing, no CLOB orders. All positions are simulated locally.

## config.json keys (weatherbet.py schema — current)

`balance`, `max_bet`, `min_ev`, `max_price`, `min_volume`, `min_hours`, `max_hours`, `kelly_fraction`, `max_slippage`, `scan_interval`, `calibration_min`, `vc_key`, `sigma_f`, `sigma_c`, `max_open_positions`.

**weatherbet_v1.py uses different keys**: `entry_threshold`, `exit_threshold`, `max_trades_per_run`, `min_hours_to_resolution`, `locations`. The current `config.json` in the repo is weatherbet.py-format; v1 falls back to hardcoded defaults when its keys are missing.

## Airport coordinates — critical detail

All city coordinates in both bots are set to the **airport station** that Polymarket actually resolves on (e.g., NYC → KLGA LaGuardia, not city center). Using city-center coordinates produces 3–8°F errors that cause wrong bucket selection. Do not change these coordinates without verifying the Polymarket resolution station.

## Data directory layout (weatherbet.py, created at runtime)

```
data/
  state.json          # balance + open positions
  calibration.json    # per-city×source sigma values
  markets/
    {city}_{date}.json  # per-market: forecast snapshots (with blended/blended_sigma), price history, PnL
```

`backtest.py` replays `data/markets/` with configurable params. Use `--forward` flag to evaluate all markets with `actual_temp` (not just ones with positions).

`simulation.json` at repo root is weatherbet_v1.py-only.
