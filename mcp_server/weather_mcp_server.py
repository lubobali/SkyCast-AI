"""SkyCast-AI - a weather MCP server.

Five tools over streamable HTTP, for a Databricks Agent Bricks agent to call:

    get_current_weather          conditions right now
    get_forecast                 a day-by-day forecast
    get_outdoor_recommendation   derived advice, with the rule that produced it
    get_severe_weather_alerts    active NWS alerts        (stretch)
    compare_cities               several places at once   (stretch)

Every function below is deliberately thin. It cleans its arguments, calls one
adapter, and shapes the result. There is no `requests` import in this file and
there should never be one: all HTTP lives in weather_provider.py and
nws_client.py, and all judgement lives in recommendation.py.

**Nothing here raises.** A tool that raises hands the agent a transport-level
failure, which it can only report as "the tool broke". A tool that returns
`{"error": "..."}` hands it a sentence, which it can act on - by asking the
user to clarify a city name, by trying a different date, or by saying plainly
that the weather service is down instead of inventing a forecast. That last
one is the whole reason for the guardrail: the failure mode worth engineering
against is not a crash, it is a model quietly filling a gap with plausible
weather.

Run locally:
    python weather_mcp_server.py        # http://127.0.0.1:8000/mcp
Deploy:
    as a Databricks App, using app.yaml in this folder.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import validation
from fastmcp import FastMCP
from location import LocationError
from nws_client import AlertServiceError, NWSAlertClient
from recommendation import recommend
from secret_store import nws_user_agent
from validation import BadArgument
from weather_provider import LocationNotFound, WeatherProvider, WeatherServiceError

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
)
logger = logging.getLogger("skycast")

mcp = FastMCP(
    name="skycast-weather",
    instructions=(
        "US weather from the National Weather Service and Open-Meteo. Call "
        "get_current_weather for conditions now, get_forecast for future days, "
        "get_outdoor_recommendation for advice about clothing or plans, and "
        "get_severe_weather_alerts before advising on travel or safety. Never "
        "state a temperature, condition, or alert that did not come from one "
        "of these tools."
    ),
)


# ---------------------------------------------------------------------------
# Adapters, built once
# ---------------------------------------------------------------------------
#
# Lazily, and cached. Building them at import time would make a missing secret
# scope or an unreachable workspace into a server that will not start at all,
# and Databricks Apps reports that as an opaque deploy failure. Built on first
# use, the same problem surfaces as one tool call returning a sentence that
# names it.

_provider: WeatherProvider | None = None
_alerts: NWSAlertClient | None = None


def get_provider() -> WeatherProvider:
    global _provider
    if _provider is None:
        _provider = WeatherProvider()
    return _provider


def get_alert_client() -> NWSAlertClient:
    global _alerts
    if _alerts is None:
        _alerts = NWSAlertClient(user_agent=nws_user_agent())
    return _alerts


def _failed(exc: Exception) -> dict:
    """Turn any exception into a result the agent can reason about.

    The three categories are kept apart because the agent's correct response to
    each is different, and the system prompt tells it which is which:

        bad_request      the user can fix this by rephrasing. Ask them to.
        not_found        the place does not exist. Ask for a different one.
        service_error    nothing the user can do. Say so; do not substitute
                         background knowledge for the answer.
    """
    if isinstance(exc, (BadArgument, LocationError)) and not isinstance(exc, LocationNotFound):
        return {"error": str(exc), "error_type": "bad_request"}
    if isinstance(exc, LocationNotFound):
        return {"error": str(exc), "error_type": "not_found"}
    if isinstance(exc, (WeatherServiceError, AlertServiceError)):
        return {"error": str(exc), "error_type": "service_error"}

    # Anything else is a bug in this server. The agent gets a sentence; the
    # traceback goes to the app log, where it belongs.
    logger.exception("Unexpected failure in a tool call")
    return {
        "error": (
            "The weather server hit an unexpected internal error. Nothing was "
            "returned, so do not guess at the weather - tell the user it is "
            "unavailable."
        ),
        "error_type": "internal_error",
    }


def _alerts_or_empty(latitude: float, longitude: float) -> tuple[list[dict], str | None]:
    """Alerts for a point, plus a note if they could not be fetched.

    Alerts are supporting evidence for a recommendation, not the point of it. A
    502 from api.weather.gov should not turn "should I take a jacket" into an
    error - but it must not silently become "no alerts" either, because that
    reads as an all-clear. So the failure travels alongside the answer.
    """
    try:
        return get_alert_client().active_alerts(latitude, longitude), None
    except Exception as exc:  # noqa: BLE001 - degraded, not failed
        logger.warning("Alert lookup failed at %s,%s: %s", latitude, longitude, exc)
        return [], (
            "Severe-weather alerts could not be checked, so this advice covers "
            "the forecast only. Say so rather than implying an all-clear."
        )


# ---------------------------------------------------------------------------
# Required tool 1 - current conditions
# ---------------------------------------------------------------------------


@mcp.tool
def get_current_weather(location: str) -> dict[str, Any]:
    """Get the weather conditions right now at a US location.

    Gives temperature and apparent temperature in Fahrenheit, relative
    humidity, plain-English sky conditions, wind speed in mph with the compass
    direction it comes from, recent precipitation in millimetres, and whether
    the sun is up there right now.

    On failure this returns an "error" and an "error_type" and no weather data
    at all. Relay the error. Do not substitute your own knowledge of the
    weather for a reading the tool did not give you.

    Args:
        location: Where to look. Either "City, ST" (for example "Chicago, IL"
            or "Austin, Texas") or coordinates as "lat,lon" (for example
            "41.88,-87.63"). A bare city name is rejected on purpose, because
            more than thirty states have a Springfield.

    Returns:
        On success, a dict with:
            location            the resolved place name
            observed_at         local observation time
            temperature_f       air temperature, Fahrenheit
            feels_like_f        apparent temperature, Fahrenheit
            humidity_pct        relative humidity
            conditions          plain-English sky conditions
            wind_mph            wind speed
            wind_direction      compass point the wind comes from
            precipitation_mm    precipitation in the last interval
            is_daytime          whether the sun is up there right now
        On failure, a dict with "error" and "error_type", and no weather data.
    """
    try:
        return get_provider().current_conditions(validation.clean_location(location))
    except Exception as exc:  # noqa: BLE001 - every failure becomes a result
        return _failed(exc)


# ---------------------------------------------------------------------------
# Required tool 2 - forecast
# ---------------------------------------------------------------------------


@mcp.tool
def get_forecast(location: str, days: int = 3) -> dict[str, Any]:
    """Get a day-by-day weather forecast for a US location.

    Each day carries its date in local time, plain-English conditions, high and
    low in Fahrenheit, chance of precipitation as a percentage, expected
    precipitation in millimetres, peak wind in mph, peak UV index, and local
    sunrise and sunset.

    Use the dates this returns when you need to name a specific day to another
    tool - they are the location's own local dates, which is not necessarily
    the same day it is where you are.

    Fields may be null past about day 10, where the forecast model stops
    publishing them. **Null means unknown, not zero.** Do not read a null
    precipitation chance as a dry day.

    On failure this returns an "error" and an "error_type" and no forecast.

    Args:
        location: "City, ST" or "lat,lon".
        days: How many days to return, counting today. Clamped to 1-16 rather
            than rejected, so asking for a month returns the longest forecast
            available instead of an error.

    Returns:
        On success, a dict with the resolved location and a "days" list. Each
        day carries:
            date                            YYYY-MM-DD, in local time
            conditions                      plain-English summary
            high_f, low_f                   Fahrenheit
            precipitation_probability_pct   chance of precipitation
            precipitation_mm                expected precipitation
            max_wind_mph                    peak wind
            uv_index_max                    peak UV index
            sunrise, sunset                 local times
        Fields may be null past about day 10, where the model stops publishing
        them. Null means unknown, not zero.
        On failure, a dict with "error" and "error_type".
    """
    try:
        return get_provider().daily_forecast(
            validation.clean_location(location), validation.clean_days(days)
        )
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


# ---------------------------------------------------------------------------
# Required tool 3 - the derived judgement
# ---------------------------------------------------------------------------


def _recommendation_for(location: str, date: str | None) -> dict[str, Any]:
    """The body of get_outdoor_recommendation, as a plain function.

    compare_cities runs this once per location. It calls this rather than the
    decorated tool above because what `@mcp.tool` hands back is a FastMCP
    implementation detail: in 3.x it returns the original function, so calling
    the tool by name would work today, and would silently stop working if a
    later version returned a Tool wrapper instead. One tool calling another by
    name is a dependency on a decorator's return value, which is not something
    worth betting a working server on.
    """
    location = validation.clean_location(location)
    wanted_date = validation.clean_date(date)

    provider = get_provider()
    place = provider.resolve(location)

    # The full horizon in one request. The alternative is arithmetic on "how
    # many days ahead is that date", which needs the local date at the location
    # - not known until after the geocode, and wrong by a day if the server
    # assumes UTC. One extra kilobyte is cheaper than that bug.
    forecast = provider.daily_forecast(location, days=16, place=place)
    days = forecast["days"]

    if wanted_date is None:
        day = days[0]
    else:
        day = next((entry for entry in days if entry["date"] == wanted_date), None)
        if day is None:
            return {
                "error": (
                    f"No forecast available for {wanted_date} at "
                    f"{forecast['location']}. Available dates are "
                    f"{days[0]['date']} to {days[-1]['date']}."
                ),
                "error_type": "bad_request",
            }

    alerts, degraded = _alerts_or_empty(place.latitude, place.longitude)
    result = {
        "location": forecast["location"],
        **recommend(day, alerts),
        "active_alerts": [
            {
                "event": alert["event"],
                "severity": alert["severity"],
                "area": alert["area"],
                "headline": alert["headline"],
            }
            for alert in alerts
        ],
    }
    if degraded:
        result["warning"] = degraded
    return result


@mcp.tool
def get_outdoor_recommendation(location: str, date: str | None = None) -> dict[str, Any]:
    """Decide whether to take an umbrella, a jacket, or sunscreen, and whether
    outdoor plans are sensible.

    This does not relay the forecast. It applies fixed thresholds to it and
    reports which rule fired, so the advice can be checked and argued with:

        umbrella     precipitation probability >= 40%, or >= 1.0 mm expected
        jacket       high < 60F, or high < 70F with wind above 15 mph, because
                     wind strips away the warm air the body holds against skin
        sunscreen    peak UV index >= 6, which the WHO classes as high
        outdoor      "avoid" for a thunderstorm or an Extreme/Severe NWS alert;
                     "take care" for a high at or above 95F, wind at or above
                     25 mph, a high at or below 20F, or fog, freezing rain, or
                     heavy precipitation; otherwise "good"

    An active NWS alert outranks every number above it: a human forecaster
    naming a hazard for a named county beats a model's daily aggregate.

    Each verdict comes back as "needed" plus "because", where "because" states
    the measurement and the threshold it was compared against. Relay that
    reasoning - it is what lets the user disagree with the advice. The reply
    also carries a one-line "summary" you can read out as-is, the full
    "thresholds_applied", and any "active_alerts" that were considered.

    Any verdict may be null, which means the data needed to judge it was not
    published for that day. **Null is not "no".** Say it is not known.

    If "warning" is present, alerts could not be checked. Say so rather than
    implying an all-clear.

    On failure this returns an "error" and an "error_type" and no advice.

    Args:
        location: "City, ST" or "lat,lon".
        date: The day to advise on, as YYYY-MM-DD. Omit for today. Relative
            words like "tomorrow" are not accepted, because which day that is
            depends on the timezone where the weather is - call get_forecast
            and use a date it returns.

    Returns:
        On success, a dict with the resolved location, the date, a one-line
        "summary", the four verdicts (each with "needed" and "because"), the
        "thresholds_applied", and any active alerts that were considered.
        On failure, a dict with "error" and "error_type".
    """
    try:
        return _recommendation_for(location, date)
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


# ---------------------------------------------------------------------------
# Stretch tool 1 - severe weather alerts
# ---------------------------------------------------------------------------


@mcp.tool
def get_severe_weather_alerts(location: str, limit: int = 10) -> dict[str, Any]:
    """Get active National Weather Service alerts covering a US location.

    This is the second data source, and the one a model cannot substitute for.
    Open-Meteo forecasts the atmosphere; it does not know that a forecaster has
    issued a Flood Advisory for two named counties. Call this before advising
    on travel or on anything safety-related.

    Alerts are looked up by point, not by state, so a user in El Paso is not
    handed advisories from the Louisiana border.

    Each alert carries its event name, severity (Extreme, Severe, Moderate,
    Minor, or Unknown), urgency, certainty, headline, the counties actually
    covered, a description of what is happening, an instruction on what to do
    about it, when it takes effect, when it expires, and which office issued
    it. They come back worst first.

    An empty list means genuinely no active alerts. That is the normal case and
    is not an error.

    On failure this returns an "error" and an "error_type" and **no alerts
    list**. That is deliberate: an outage must never look like an all-clear.
    Tell the user alerts could not be checked.

    Args:
        location: "City, ST" or "lat,lon".
        limit: Most alerts to return, most severe first. Clamped to 1-20.

    Returns:
        On success, a dict with the resolved location, a "count", and an
        "alerts" list ordered worst first. Each alert carries event, severity
        (Extreme, Severe, Moderate, Minor, or Unknown), urgency, certainty,
        headline, area (the counties actually covered), description (what is
        happening), instruction (what to do about it), effective, expires, and
        issued_by. An empty list means genuinely no active alerts, which is
        normal and is not an error.
        On failure, a dict with "error" and "error_type".
    """
    try:
        provider = get_provider()
        place = provider.resolve(validation.clean_location(location))
        alerts = get_alert_client().active_alerts(
            place.latitude, place.longitude, limit=validation.clean_limit(limit)
        )
        return {
            "location": place.label,
            "count": len(alerts),
            "alerts": alerts,
        }
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


# ---------------------------------------------------------------------------
# Stretch tool 2 - compare several places
# ---------------------------------------------------------------------------


@mcp.tool
def compare_cities(locations: list[str], date: str | None = None) -> dict[str, Any]:
    """Compare the forecast and outdoor advice across several US locations.

    For questions like "where should we go this weekend" or "I am driving from
    Denver to Miami, what will I hit". Up to five places per call.

    Each entry carries the location, the date, conditions, high and low in
    Fahrenheit, the recommendation summary, the outdoor advice ("avoid", "take
    care", "good", or "unknown"), and any active alerts there.

    One location failing does not lose the rest: its error is reported in the
    "errors" list and the others still come back. **Always check "errors"** -
    a place that appears in neither list was silently dropped, and reporting on
    four cities when the user asked about five is a wrong answer.

    Args:
        locations: Two to five places, each "City, ST" or "lat,lon".
        date: The day to compare, as YYYY-MM-DD. Omit for today.

    Returns:
        On success, a dict with a "comparison" list - one entry per location
        carrying its date, conditions, high_f, low_f, the recommendation
        summary, the outdoor advice, and any active alerts - plus an "errors"
        list naming any location that could not be resolved.
        On failure of the call itself, a dict with "error" and "error_type".
    """
    try:
        places = validation.clean_locations(locations)
        wanted_date = validation.clean_date(date)
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)

    comparison: list[dict] = []
    errors: list[dict] = []

    for location in places:
        try:
            result = _recommendation_for(location, wanted_date)
        except Exception as exc:  # noqa: BLE001 - one bad city, not a bad trip
            errors.append({"location": location, **_failed(exc)})
            continue
        if "error" in result:
            errors.append({"location": location, **result})
            continue
        comparison.append(
            {
                "location": result["location"],
                "date": result["date"],
                "conditions": result["conditions"],
                "high_f": result["high_f"],
                "low_f": result["low_f"],
                "summary": result["summary"],
                "outdoor_advice": result["outdoor_activity"]["advice"],
                "active_alerts": result["active_alerts"],
            }
        )

    return {
        "date": wanted_date or "today",
        "compared": len(comparison),
        "comparison": comparison,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Two plain HTTP routes, outside the MCP protocol
# ---------------------------------------------------------------------------
#
# An MCP server mounts one path, /mcp, and answers only MCP protocol requests
# on it. Opening the app's URL in a browser therefore returns "URL Not Found",
# which is correct and completely unhelpful - it looks identical to a failed
# deploy. These two routes exist so that a human who opens the URL learns what
# the thing is, and so a platform probe has something to hit.


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request):
    """Liveness, plus where the NWS contact string came from.

    Deliberately does not call a weather API. A health check that depends on
    Open-Meteo reports this server unhealthy during somebody else's outage, and
    gets it restarted for no reason.

    It does report `nws_contact`, which is the one thing about this deployment
    that is otherwise unobservable. Both the secret and the fallback are valid
    contact strings that api.weather.gov accepts, so a server quietly running on
    the fallback - because the secret scope was never created, or the app's
    service principal was never granted READ on it - behaves identically to one
    reading the secret. `"secret"` here is the proof that the whole path works.

    The source, never the value: the value carries a personal email address, and
    this endpoint is reachable by anyone who can reach the app.
    """
    from secret_store import KEY, SCOPE, resolve_user_agent
    from starlette.responses import JSONResponse

    return JSONResponse(
        {
            "status": "ok",
            "server": "skycast-weather",
            "tools": len(_TOOL_NAMES),
            "nws_contact": resolve_user_agent()[1],
            "secret": f"{SCOPE}/{KEY}",
        }
    )


@mcp.custom_route("/", methods=["GET"])
async def index(request):
    """A landing page saying what this URL is and what lives on it."""
    from starlette.responses import HTMLResponse

    rows = "\n".join(
        f"<tr><td><code>{name}</code></td><td>{summary}</td></tr>"
        for name, summary in _TOOL_SUMMARIES
    )
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>SkyCast-AI - weather MCP server</title>
<style>
  body {{ font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 46rem; margin: 3rem auto; padding: 0 1.5rem;
         background: #0d1117; color: #c9d1d9; }}
  h1 {{ margin-bottom: .2rem; }}
  .sub {{ color: #8b949e; margin-top: 0; }}
  code {{ background: #161b22; padding: .1rem .35rem; border-radius: 4px;
          color: #79c0ff; font-size: .9em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1.2rem 0; }}
  td {{ padding: .5rem .6rem; border-top: 1px solid #21262d; vertical-align: top; }}
  td:first-child {{ white-space: nowrap; }}
  .note {{ background: #161b22; border-left: 3px solid #388bfd;
           padding: .8rem 1rem; border-radius: 0 6px 6px 0; }}
  a {{ color: #79c0ff; }}
</style></head><body>
<h1>SkyCast-AI</h1>
<p class="sub">A weather MCP server. US current conditions, forecasts,
outdoor recommendations, and National Weather Service alerts.</p>

<div class="note">
This is not a website. It is an <strong>MCP server</strong>, and it speaks the
Model Context Protocol at <code>/mcp</code> - not HTML. It is meant to be
called by an agent, not browsed. Registered in Databricks under
AI&nbsp;Gateway&nbsp;&rarr;&nbsp;MCPs as <code>skycast-weather</code>.
</div>

<h2>Tools</h2>
<table>{rows}</table>

<p>Sources: <a href="https://open-meteo.com">Open-Meteo</a> for conditions and
forecasts, <a href="https://www.weather.gov/documentation/services-web-api">api.weather.gov</a>
for severe-weather alerts. Neither needs an API key.</p>

<p>Source: <a href="https://github.com/lubobali/SkyCast-AI">github.com/lubobali/SkyCast-AI</a>
&nbsp;&middot;&nbsp; <a href="/healthz">/healthz</a></p>
</body></html>"""
    )


def main() -> None:
    # Databricks Apps injects the port to listen on and expects the process to
    # bind every interface. PORT covers local runs and most platforms;
    # DATABRICKS_APP_PORT is what Apps actually sets.
    port = int(
        os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("PORT") or 8000
    )
    host = os.environ.get("HOST", "0.0.0.0")

    logger.info("SkyCast-AI MCP server starting on %s:%s", host, port)
    logger.info("Tools: %s", ", ".join(sorted(_TOOL_NAMES)))
    mcp.run(transport="streamable-http", host=host, port=port)


_TOOL_SUMMARIES = (
    ("get_current_weather", "Conditions right now: temperature, humidity, wind, sky."),
    ("get_forecast", "Up to 16 days: highs, lows, chance of rain, wind, UV."),
    (
        "get_outdoor_recommendation",
        (
            "Umbrella, jacket, sunscreen, and whether outdoor plans are sensible - "
            "each with the threshold that decided it."
        ),
    ),
    ("get_severe_weather_alerts", "Active National Weather Service alerts for a point."),
    ("compare_cities", "Two to five places in one call."),
)

_TOOL_NAMES = {name for name, _ in _TOOL_SUMMARIES}


if __name__ == "__main__":
    main()
