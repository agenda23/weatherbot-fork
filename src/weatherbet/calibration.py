"""calibration.py — Per-city/source sigma management and RMSE recalculation."""

import json
import math
from datetime import datetime, timezone

from weatherbet.config import (
    CALIBRATION_FILE,
    CALIBRATION_MIN,
    SIGMA_F,
    SIGMA_C,
    LOCATIONS,
)
from weatherbet.notify import log_event

_cal: dict = {}


def load_cal() -> dict:
    if CALIBRATION_FILE.exists():
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    return {}


def init_cal():
    """Load calibration data from disk into the module-level cache."""
    global _cal
    _cal = load_cal()


def get_sigma(city_slug: str, source: str = "ecmwf") -> float:
    key = f"{city_slug}_{source}"
    if key in _cal:
        return _cal[key]["sigma"]
    return SIGMA_F if LOCATIONS[city_slug]["unit"] == "F" else SIGMA_C


def run_calibration(markets: list) -> dict:
    """Recalculates sigma from resolved markets using RMSE."""
    global _cal
    resolved = [m for m in markets if m.get("resolved") and m.get("actual_temp") is not None]
    cal = load_cal()
    updated = []

    for source in ["ecmwf", "hrrr", "metar"]:
        for city in set(m["city"] for m in resolved):
            group = [m for m in resolved if m["city"] == city]
            errors = []
            for m in group:
                snap = next(
                    (s for s in reversed(m.get("forecast_snapshots", [])) if s["source"] == source),
                    None,
                )
                if snap and snap.get("temp") is not None:
                    errors.append(abs(snap["temp"] - m["actual_temp"]))
            if len(errors) < CALIBRATION_MIN:
                continue
            rmse = math.sqrt(sum(e ** 2 for e in errors) / len(errors))
            key = f"{city}_{source}"
            old = cal.get(key, {}).get("sigma", SIGMA_F if LOCATIONS[city]["unit"] == "F" else SIGMA_C)
            new = round(rmse, 3)
            cal[key] = {"sigma": new, "n": len(errors), "updated_at": datetime.now(timezone.utc).isoformat()}
            if abs(new - old) > 0.05:
                updated.append(f"{LOCATIONS[city]['name']} {source}: {old:.2f}->{new:.2f}")

    CALIBRATION_FILE.write_text(json.dumps(cal, indent=2), encoding="utf-8")
    if updated:
        log_event("INFO", f"[CAL] {', '.join(updated)}", updated=updated)
    _cal = cal
    return cal
