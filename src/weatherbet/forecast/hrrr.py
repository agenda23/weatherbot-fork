"""hrrr.py — HRRR/GFS forecast via Open-Meteo (US cities only)."""

import time
import requests

from weatherbet.config import LOCATIONS, TIMEZONES
from weatherbet.notify import track_api_result, log_event


def get_hrrr(city_slug, dates):
    """HRRR via Open-Meteo. US cities only, up to 48h horizon."""
    loc = LOCATIONS[city_slug]
    if loc["region"] != "us":
        return {}
    result = {}
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit=fahrenheit"
        f"&forecast_days=3&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models=gfs_seamless"
    )
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data:
                for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                    if date in dates and temp is not None:
                        result[date] = round(temp)
            track_api_result("open_meteo_hrrr", True)
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                track_api_result("open_meteo_hrrr", False, str(e))
                log_event("WARNING", f"[HRRR] {city_slug}: {e}", city=city_slug, source="hrrr")
    return result
