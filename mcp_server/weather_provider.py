"""Open-Meteo adapter: every HTTP call and every parse for current conditions
and forecasts lives here.

This is the module the homework calls "a separate adapter module, like
alpaca_broker.py". The rule it exists to enforce: **no raw HTTP anywhere near
an @mcp.tool function.** A tool reads an argument, calls one method here, and
shapes the result. If a tool ever needs `requests`, something belongs in this
file instead.

Why Open-Meteo. It needs no signup, no key, and no credit card, so the whole
pipeline can be built and tested before secrets management enters the picture
at all. It also carries the two fields the recommendation logic actually needs
and that many free APIs omit: a per-day precipitation *probability*, and a UV
index.

What it returns. Plain dicts with units in the key names - `temperature_f`,
`wind_mph`, `precipitation_probability_pct`. The consumer is a language model
deciding whether to tell someone to take a coat. A bare `temperature: 78.8` is
an invitation to guess at a unit, and the guess is invisible when it is wrong.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from http_client import HttpError, JsonHttpClient
from location import COORD_PRECISION, LocationError, ParsedLocation, normalize_state, parse_location

logger = logging.getLogger(__name__)

GEOCODER_URL = os.environ.get(
    "GEOCODER_URL", "https://geocoding-api.open-meteo.com/v1/search"
)
FORECAST_URL = os.environ.get("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")

# Open-Meteo serves at most 16 days. Asking for more is a 400, so the ceiling
# is enforced here rather than discovered upstream.
MAX_FORECAST_DAYS = int(os.environ.get("MAX_FORECAST_DAYS", "16"))

_CURRENT_FIELDS = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
    "weather_code,wind_speed_10m,wind_direction_10m,is_day"
)
_DAILY_FIELDS = (
    "weather_code,temperature_2m_max,temperature_2m_min,"
    "precipitation_probability_max,precipitation_sum,wind_speed_10m_max,"
    "uv_index_max,sunrise,sunset"
)

# WMO 4677 present-weather codes. Open-Meteo returns the integer; an agent
# needs the words. Left as an explicit table rather than a range lookup so
# that an unmapped code is visibly unmapped instead of quietly bucketed into
# whatever neighbour a range happened to cover.
WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Freezing fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Light rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Light snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with light hail",
    99: "Thunderstorm with heavy hail",
}

_COMPASS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


class LocationNotFound(LocationError):
    """The place name is well formed but no US location matches it.

    A subclass of LocationError because callers handle both the same way: tell
    the user, and ask for a different string. Distinct so a tool can say
    "I could not find that place" rather than "that is not a valid location",
    which are different problems from the user's side of the conversation.
    """


class WeatherServiceError(RuntimeError):
    """The upstream weather service failed. Not the user's fault, and not
    something a better location string would fix."""


@dataclass(frozen=True)
class Place:
    """A location pinned to coordinates."""

    label: str
    latitude: float
    longitude: float
    state: str | None = None
    timezone: str | None = None


def describe_code(code: Any) -> str:
    """Turn a WMO weather code into words.

    An unknown code is reported as itself rather than as "Unknown", so a gap in
    the table is traceable to the exact integer that caused it.
    """
    try:
        return WMO_CODES[int(code)]
    except (KeyError, TypeError, ValueError):
        return f"Unrecognized conditions (WMO code {code})"


def describe_bearing(degrees: Any) -> str | None:
    """Turn a wind bearing in degrees into a compass point."""
    try:
        value = float(degrees)
    except (TypeError, ValueError):
        return None
    return _COMPASS[round(value / 22.5) % 16]


class WeatherProvider:
    """Current conditions and forecasts from Open-Meteo."""

    def __init__(
        self,
        *,
        geocoder_url: str | None = None,
        forecast_url: str | None = None,
        http: JsonHttpClient | None = None,
        **http_kwargs: Any,
    ) -> None:
        self.geocoder_url = geocoder_url or GEOCODER_URL
        self.forecast_url = forecast_url or FORECAST_URL
        # Open-Meteo asks for no pacing and sets no User-Agent requirement, so
        # the shared client is used with its rate limiter switched off.
        self.http = http or JsonHttpClient(**http_kwargs)

    # -- resolution ---------------------------------------------------------

    def _geocode(self, parsed: ParsedLocation) -> Place:
        """Resolve "City, ST" to coordinates, honouring the state that was asked for.

        Open-Meteo returns matches ranked by population, so the first result for
        "Springfield" is whichever Springfield is biggest - not the one the user
        named a state for. Every candidate is therefore filtered on country and
        state before one is accepted.
        """
        try:
            payload = self.http.get_json(
                self.geocoder_url,
                params={
                    "name": parsed.city,
                    "count": 10,
                    "countryCode": "US",
                    "language": "en",
                    "format": "json",
                },
            )
        except HttpError as exc:
            raise WeatherServiceError(f"Could not look up {parsed.label}: {exc}") from exc

        for result in payload.get("results") or []:
            if (result.get("country_code") or "").upper() != "US":
                continue
            if normalize_state(result.get("admin1") or "") != parsed.state:
                continue
            return Place(
                label=f"{result.get('name') or parsed.city}, {parsed.state}",
                latitude=round(float(result["latitude"]), COORD_PRECISION),
                longitude=round(float(result["longitude"]), COORD_PRECISION),
                state=parsed.state,
                timezone=result.get("timezone"),
            )

        raise LocationNotFound(
            f"No US place found matching {parsed.label}. Check the spelling, or "
            "pass coordinates as 'lat,lon'."
        )

    def resolve(self, location: str) -> Place:
        """Resolve a user-supplied location string to coordinates.

        Args:
            location: "City, ST", "City, State Name", or "lat,lon".

        Returns:
            A Place carrying coordinates and, when known, the state.

        Raises:
            LocationError: the string is malformed.
            LocationNotFound: it is well formed but matches no US place.
            WeatherServiceError: the geocoder itself failed.
        """
        parsed = parse_location(location)
        if parsed.is_coordinates:
            return Place(
                label=parsed.label,
                latitude=float(parsed.latitude),
                longitude=float(parsed.longitude),
                state=None,
                timezone=None,
            )
        return self._geocode(parsed)

    # -- fetching -----------------------------------------------------------

    def _forecast_payload(self, place: Place, *, current: bool, days: int) -> dict:
        params: dict[str, Any] = {
            "latitude": place.latitude,
            "longitude": place.longitude,
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            # Timestamps come back in the location's own timezone. A forecast
            # for "tomorrow" in Denver that is labelled in UTC is off by a day
            # for exactly the evening hours people ask about.
            "timezone": "auto",
        }
        if current:
            params["current"] = _CURRENT_FIELDS
        if days:
            params["daily"] = _DAILY_FIELDS
            params["forecast_days"] = days

        try:
            return self.http.get_json(self.forecast_url, params=params)
        except HttpError as exc:
            raise WeatherServiceError(
                f"Could not get weather for {place.label}: {exc}"
            ) from exc

    def current_conditions(self, location: str, *, place: Place | None = None) -> dict:
        """Conditions right now at `location`.

        Args:
            location: "City, ST" or "lat,lon".
            place: An already-resolved Place, to skip the geocode. A tool that
                needs both weather and alerts resolves once and passes the
                result to both, rather than geocoding the same city twice in
                one call.

        Returns:
            A dict with temperature_f, feels_like_f, humidity_pct, conditions,
            wind_mph, wind_direction, precipitation_mm, is_daytime, plus the
            resolved place and the observation time in local time.

        Raises:
            LocationError / LocationNotFound / WeatherServiceError.
        """
        place = place or self.resolve(location)
        payload = self._forecast_payload(place, current=True, days=0)
        current = payload.get("current") or {}
        if not current:
            raise WeatherServiceError(
                f"Weather service returned no current observation for {place.label}."
            )

        return {
            "location": place.label,
            "latitude": place.latitude,
            "longitude": place.longitude,
            "timezone": payload.get("timezone"),
            "observed_at": current.get("time"),
            "temperature_f": current.get("temperature_2m"),
            "feels_like_f": current.get("apparent_temperature"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "conditions": describe_code(current.get("weather_code")),
            "wind_mph": current.get("wind_speed_10m"),
            "wind_direction": describe_bearing(current.get("wind_direction_10m")),
            "precipitation_mm": current.get("precipitation"),
            "is_daytime": bool(current.get("is_day")),
        }

    def daily_forecast(
        self, location: str, days: int = 3, *, place: Place | None = None
    ) -> dict:
        """A day-by-day forecast for `location`.

        Args:
            location: "City, ST" or "lat,lon".
            days: How many days, counting today. Clamped to 1..MAX_FORECAST_DAYS.
            place: An already-resolved Place, to skip the geocode.

        Returns:
            A dict with the resolved place and a `days` list, each entry
            carrying date, conditions, high_f, low_f,
            precipitation_probability_pct, precipitation_mm, max_wind_mph,
            uv_index_max, sunrise, and sunset.

        Raises:
            LocationError / LocationNotFound / WeatherServiceError.
        """
        wanted = max(1, min(int(days), MAX_FORECAST_DAYS))
        place = place or self.resolve(location)
        payload = self._forecast_payload(place, current=False, days=wanted)

        daily = payload.get("daily") or {}
        dates = daily.get("time") or []
        if not dates:
            raise WeatherServiceError(
                f"Weather service returned no forecast for {place.label}."
            )

        def column(name: str) -> list:
            # Open-Meteo answers column-wise: one parallel array per field.
            # A short array means that field is unavailable for the tail of the
            # range, which is normal at the edge of the model's horizon - so it
            # is padded rather than allowed to shorten the whole forecast.
            values = daily.get(name) or []
            return list(values) + [None] * (len(dates) - len(values))

        codes = column("weather_code")
        highs = column("temperature_2m_max")
        lows = column("temperature_2m_min")
        precip_probability = column("precipitation_probability_max")
        precip_sum = column("precipitation_sum")
        winds = column("wind_speed_10m_max")
        uv = column("uv_index_max")
        sunrise = column("sunrise")
        sunset = column("sunset")

        return {
            "location": place.label,
            "latitude": place.latitude,
            "longitude": place.longitude,
            "timezone": payload.get("timezone"),
            "days": [
                {
                    "date": date,
                    "conditions": describe_code(codes[index]),
                    # The raw code travels alongside its translation. The
                    # recommendation logic keys off the number rather than
                    # pattern-matching the English, so adding a phrase to
                    # WMO_CODES can never quietly change a verdict.
                    "weather_code": codes[index],
                    "high_f": highs[index],
                    "low_f": lows[index],
                    "precipitation_probability_pct": precip_probability[index],
                    "precipitation_mm": precip_sum[index],
                    "max_wind_mph": winds[index],
                    "uv_index_max": uv[index],
                    "sunrise": sunrise[index],
                    "sunset": sunset[index],
                }
                for index, date in enumerate(dates)
            ],
        }


__all__ = [
    "MAX_FORECAST_DAYS",
    "WMO_CODES",
    "LocationNotFound",
    "Place",
    "WeatherProvider",
    "WeatherServiceError",
    "describe_bearing",
    "describe_code",
]
