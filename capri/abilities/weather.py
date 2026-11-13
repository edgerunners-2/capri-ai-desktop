"""Weather from Open-Meteo (free, no key): right now, the rest of today, tomorrow, for your city or any city you name.
Your city is whatever you told her ("my city is Casablanca"), kept in her settings."""
from __future__ import annotations

_CODES = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
          51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
          61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow",
          73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
          82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
          96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def _get(url: str, params: dict) -> dict:
    import requests

    r = requests.get(url, params=params, timeout=10, headers={"User-Agent": "CapriDesktopCompanion/0.2"})
    r.raise_for_status()
    return r.json()


def place(city: str) -> dict | None:
    """'Casablanca' -> {'name', 'country', 'latitude', 'longitude', 'timezone'} (None if no such place)."""
    try:
        hits = _get("https://geocoding-api.open-meteo.com/v1/search",
                    {"name": city, "count": 1, "language": "en", "format": "json"}).get("results") or []
        return hits[0] if hits else None
    except Exception as e:
        print(f"[capri] weather: couldn't find {city}: {e}")
        return None


def report(city: str) -> str | None:
    """A short plain-text weather report for her to answer from. None when the city isn't found or it's offline."""
    p = place(city)
    if not p:
        return None
    try:
        d = _get("https://api.open-meteo.com/v1/forecast", {
            "latitude": p["latitude"], "longitude": p["longitude"], "timezone": "auto", "forecast_days": 2,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max,"
                     "wind_speed_10m_max"})
    except Exception as e:
        print(f"[capri] weather failed: {e}")
        return None
    c, dl = d["current"], d["daily"]
    where = f"{p['name']}, {p.get('country', '')}".strip(", ")

    def day(i: int) -> str:
        return (f"{_CODES.get(dl['weather_code'][i], 'mixed')}, {round(dl['temperature_2m_min'][i])} to "
                f"{round(dl['temperature_2m_max'][i])}°C, {dl['precipitation_probability_max'][i]}% chance of rain, "
                f"wind up to {round(dl['wind_speed_10m_max'][i])} km/h")

    return (f"Weather in {where}: right now {round(c['temperature_2m'])}°C (feels like "
            f"{round(c['apparent_temperature'])}°C), {_CODES.get(c['weather_code'], 'mixed')}, wind "
            f"{round(c['wind_speed_10m'])} km/h. Today: {day(0)}. Tomorrow: {day(1)}.")
