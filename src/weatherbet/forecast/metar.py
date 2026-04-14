"""metar.py — METAR real-time observations and Visual Crossing actual temps."""

import requests

from weatherbet.config import LOCATIONS, VC_KEY
from weatherbet.notify import track_api_result, log_event


def get_metar(city_slug):
    """Current observed temperature from METAR station. D+0 only."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=json"
        data = requests.get(url, timeout=(5, 8)).json()
        if data and isinstance(data, list):
            temp_c = data[0].get("temp")
            if temp_c is not None:
                track_api_result("aviationweather_metar", True)
                if unit == "F":
                    return round(float(temp_c) * 9 / 5 + 32)
                return round(float(temp_c), 1)
        track_api_result("aviationweather_metar", True)
    except Exception as e:
        track_api_result("aviationweather_metar", False, str(e))
        log_event("WARNING", f"[METAR] {city_slug}: {e}", city=city_slug, source="metar")
    return None


def get_actual_temp(city_slug, date_str):
    """Actual temperature via Visual Crossing for closed markets."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    vc_unit = "us" if unit == "F" else "metric"
    url = (
        f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
        f"/{station}/{date_str}/{date_str}"
        f"?unitGroup={vc_unit}&key={VC_KEY}&include=days&elements=tempmax"
    )
    try:
        data = requests.get(url, timeout=(5, 8)).json()
        days = data.get("days", [])
        if days and days[0].get("tempmax") is not None:
            track_api_result("visualcrossing", True)
            return round(float(days[0]["tempmax"]), 1)
        track_api_result("visualcrossing", True)
    except Exception as e:
        track_api_result("visualcrossing", False, str(e))
        log_event("WARNING", f"[VC] {city_slug} {date_str}: {e}", city=city_slug, date=date_str, source="vc")
    return None
