"""blend.py — Ensemble blending and forecast snapshot."""

import math
from datetime import datetime, timezone, timedelta

from weatherbet.config import LOCATIONS
from weatherbet.calibration import get_sigma
from weatherbet.forecast.ecmwf import get_ecmwf
from weatherbet.forecast.hrrr import get_hrrr
from weatherbet.forecast.metar import get_metar


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


def take_forecast_snapshot(city_slug, dates):
    """Fetches forecasts from all sources and returns a snapshot dict keyed by date."""
    now_str = datetime.now(timezone.utc).isoformat()
    ecmwf = get_ecmwf(city_slug, dates)
    hrrr = get_hrrr(city_slug, dates)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    snapshots = {}
    for date in dates:
        snap = {
            "ts":    now_str,
            "ecmwf": ecmwf.get(date),
            "hrrr":  hrrr.get(date) if date <= (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d") else None,
            "metar": get_metar(city_slug) if date == today else None,
        }
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
