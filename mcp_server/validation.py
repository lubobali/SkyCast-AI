"""Cleaning what the agent passes in.

An MCP tool's caller is a language model, which makes the input a different
shape of untrusted than a web form. It will not attempt an injection, but it
will confidently pass `days="three"`, or `top_k=500`, or a location with a
trailing newline, or `2026-8-9` instead of `2026-08-09` - and it does this
without any signal that it has guessed.

So every argument is coerced and clamped here, once, before it reaches an
adapter. The alternative is each tool re-deriving the same defence, which is
how one tool ends up accepting `days=0` while its neighbour rejects it.

Errors raised here are worded for the model to read and act on, because that is
literally what happens to them: the tool returns the message, and the agent
either fixes its call or relays the message to the user.
"""

from __future__ import annotations

import re
from datetime import date as date_type
from typing import Any

MAX_COMPARE_LOCATIONS = 5
"""Ceiling on compare_cities. Each location costs a geocode plus a forecast, so
ten cities is twenty round trips and a tool call slow enough that the agent
gives up waiting."""

MAX_ALERTS = 20
DEFAULT_FORECAST_DAYS = 3

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


class BadArgument(ValueError):
    """A tool argument could not be used. The message is written for the caller."""


def clean_location(value: Any, field: str = "location") -> str:
    """Require a non-empty string. Parsing into coordinates happens downstream.

    Deliberately does not validate the *format* here - location.parse_location
    owns that, and owns the error message that teaches the accepted forms.
    Duplicating it would mean two places to keep in step.
    """
    if not isinstance(value, str):
        raise BadArgument(
            f"{field} must be text like 'Chicago, IL', got {type(value).__name__}."
        )
    cleaned = value.strip()
    if not cleaned:
        raise BadArgument(f"{field} must not be empty. Use 'City, ST' or 'lat,lon'.")
    return cleaned


def clean_locations(value: Any) -> list[str]:
    """A list of locations for a comparison, deduplicated and capped.

    Accepts a real list, or one semicolon- or comma-free string. Agents pass
    both, and rejecting the string form produces a retry loop rather than an
    answer.
    """
    if isinstance(value, str):
        items = [part for part in value.split(";")]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise BadArgument(
            "locations must be a list like ['Chicago, IL', 'Austin, TX'], got "
            f"{type(value).__name__}."
        )

    cleaned: list[str] = []
    for item in items:
        location = clean_location(item, field="each location")
        # Case-insensitive dedup: an agent comparing "Chicago, IL" with
        # "chicago, il" is asking one question, not two.
        if location.lower() not in {existing.lower() for existing in cleaned}:
            cleaned.append(location)

    if not cleaned:
        raise BadArgument("locations must contain at least one place.")
    if len(cleaned) < 2:
        raise BadArgument(
            "compare_cities needs at least two places. For one, use get_forecast."
        )
    return cleaned[:MAX_COMPARE_LOCATIONS]


def clean_days(value: Any, default: int = DEFAULT_FORECAST_DAYS, maximum: int = 16) -> int:
    """Coerce a day count, clamped to 1..maximum.

    Clamps rather than rejects. `days=30` is a reasonable thing to ask and an
    unreasonable thing to fail on, and Open-Meteo's answer to it is a 400 that
    tells the agent nothing about what it should have asked for instead.
    """
    if value is None or value == "":
        return default
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise BadArgument(
            f"days must be a whole number of days, got {value!r}."
        ) from None
    return max(1, min(days, maximum))


def clean_limit(value: Any, default: int = 10, maximum: int = MAX_ALERTS) -> int:
    """Coerce a result count, clamped to 1..maximum."""
    if value is None or value == "":
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise BadArgument(f"limit must be a whole number, got {value!r}.") from None
    return max(1, min(limit, maximum))


def clean_date(value: Any) -> str | None:
    """Normalize a requested date to YYYY-MM-DD, or None for "the next day available".

    Accepts a real date, an ISO string, and the single-digit forms a model
    reaches for ("2026-8-9"). Relative words are deliberately *not* accepted:
    "tomorrow" depends on the timezone at the location being asked about, which
    is not known until after it has been geocoded, and a server that guesses
    UTC is wrong for exactly the evening hours people ask about. The tools
    resolve relative days by position in the forecast instead.
    """
    if value is None or value == "":
        return None

    if isinstance(value, date_type):
        return value.isoformat()

    if not isinstance(value, str):
        raise BadArgument(f"date must be text like '2026-08-09', got {type(value).__name__}.")

    text = value.strip()
    if not text:
        return None

    match = _ISO_DATE_RE.match(text)
    if not match:
        raise BadArgument(
            f"Could not read {value!r} as a date. Use YYYY-MM-DD, for example "
            "'2026-08-09'. Relative words like 'tomorrow' are not accepted here - "
            "call get_forecast and read the dates it returns."
        )

    year, month, day = (int(part) for part in match.groups())
    try:
        return date_type(year, month, day).isoformat()
    except ValueError as exc:
        raise BadArgument(f"{value!r} is not a real date ({exc}).") from None


__all__ = [
    "DEFAULT_FORECAST_DAYS",
    "MAX_ALERTS",
    "MAX_COMPARE_LOCATIONS",
    "BadArgument",
    "clean_date",
    "clean_days",
    "clean_limit",
    "clean_location",
    "clean_locations",
]
