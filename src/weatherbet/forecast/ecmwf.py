"""ecmwf.py — ECMWF forecast via Open-Meteo."""

import time
import requests

from weatherbet.config import LOCATIONS, TIMEZONES
from weatherbet.notify import track_api_result, log_event


def get_ecmwf(city_slug, dates):
    """ECMWF via Open-Meteo with bias correction. For all cities."""
    loc = LOCATIONS[city_slug]
    unit = loc["unit"]
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    result = {}
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
        f"&forecast_days=7&timezone={TIMEZONES.get(city_slug, 'UTC')}"
        f"&models=ecmwf_ifs025&bias_correction=true"
    )
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data:
                for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                    if date in dates and temp is not None:
                        result[date] = round(temp, 1) if unit == "C" else round(temp)
            track_api_result("open_meteo_ecmwf", True)
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                track_api_result("open_meteo_ecmwf", False, str(e))
                log_event("WARNING", f"[ECMWF] {city_slug}: {e}", city=city_slug, source="ecmwf")
    return result
