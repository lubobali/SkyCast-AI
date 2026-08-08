"""Turning what a user typed into something a weather API will accept.

Both adapters need this and neither owns it, so it lives on its own. The MCP
tools take a free-text `location` argument, which in practice arrives as one of
three things:

    "Chicago, IL"        a city and a state
    "Chicago, Illinois"  the same, spelled out
    "41.8781,-87.6298"   coordinates, usually from another tool's output

A bare city name is rejected on purpose. "Springfield" alone names places in
more than thirty states, and quietly picking one returns confident weather for
the wrong part of the country - the worst failure mode available here, because
nothing about the answer looks wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Open-Meteo and api.weather.gov both accept far more precision than this, but
# api.weather.gov answers 301 for /points coordinates carrying more than four
# decimals and 200 at exactly four. Four decimals is roughly eleven metres,
# which is well inside the resolution of any forecast grid, so rounding costs
# nothing real and saves a redirect on every lookup.
COORD_PRECISION = 4

_COORD_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*$")

US_STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME",
    "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE",
    "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    # NWS covers these too, and they get real alerts.
    "PUERTO RICO": "PR", "GUAM": "GU", "AMERICAN SAMOA": "AS",
    "VIRGIN ISLANDS": "VI", "U.S. VIRGIN ISLANDS": "VI",
    "NORTHERN MARIANA ISLANDS": "MP",
}
_STATE_CODES = set(US_STATES.values())


class LocationError(ValueError):
    """The location string cannot be used.

    A ValueError rather than a bespoke hierarchy root, because every caller
    treats it the same way: report it to the user and ask for a better string.
    The message is written to be shown verbatim.
    """


@dataclass(frozen=True)
class ParsedLocation:
    """A location request, before it has touched the network."""

    raw: str
    is_coordinates: bool
    latitude: float | None = None
    longitude: float | None = None
    city: str | None = None
    state: str | None = None

    @property
    def label(self) -> str:
        if self.is_coordinates:
            return f"{self.latitude},{self.longitude}"
        return f"{self.city}, {self.state}"


def normalize_state(value: str) -> str | None:
    """Map "IL" or "Illinois" to the two-letter code the weather APIs use."""
    candidate = (value or "").strip().upper()
    if candidate in _STATE_CODES:
        return candidate
    return US_STATES.get(candidate)


def parse_location(raw: object) -> ParsedLocation:
    """Parse a user-supplied location into coordinates or a city and state.

    Args:
        raw: What the user typed. "City, ST", "City, State Name", or "lat,lon".

    Returns:
        A ParsedLocation. Nothing has been looked up yet; this only decides
        what kind of request it is and whether it is well formed.

    Raises:
        LocationError: The string is empty, is not a string, names no state,
            or carries out-of-range coordinates. The message names the problem
            and shows the accepted forms.

    >>> parse_location("Chicago, IL").city
    'Chicago'
    >>> parse_location("Chicago, Illinois").state
    'IL'
    >>> parse_location("41.8781,-87.6298").is_coordinates
    True
    """
    if not isinstance(raw, str):
        raise LocationError(
            f"Location must be text like 'Chicago, IL', got {type(raw).__name__}"
        )

    text = raw.strip()
    if not text:
        raise LocationError("Location must not be empty. Use 'City, ST' or 'lat,lon'.")

    coords = _COORD_RE.match(text)
    if coords:
        latitude, longitude = float(coords.group(1)), float(coords.group(2))
        if not -90.0 <= latitude <= 90.0:
            raise LocationError(f"Latitude {latitude} is outside the range -90 to 90")
        if not -180.0 <= longitude <= 180.0:
            raise LocationError(f"Longitude {longitude} is outside the range -180 to 180")
        return ParsedLocation(
            raw=text,
            is_coordinates=True,
            latitude=round(latitude, COORD_PRECISION),
            longitude=round(longitude, COORD_PRECISION),
        )

    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 2 or not parts[0]:
        raise LocationError(
            f"Could not read {raw!r} as a location. Use 'City, ST' (for example "
            "'Chicago, IL') or 'lat,lon' (for example '41.88,-87.63')."
        )

    city, state_raw = parts
    state = normalize_state(state_raw)
    if not state:
        raise LocationError(
            f"{state_raw!r} is not a US state or territory (from {raw!r}). "
            "This server covers the United States only."
        )

    return ParsedLocation(raw=text, is_coordinates=False, city=city, state=state)


__all__ = [
    "COORD_PRECISION",
    "US_STATES",
    "LocationError",
    "ParsedLocation",
    "normalize_state",
    "parse_location",
]
