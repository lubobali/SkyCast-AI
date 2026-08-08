"""Derived judgement: turning a forecast into advice.

This is the module the homework singles out. The prediction tool has to *"do
more than echo the raw API"* and has to *"apply some threshold/logic of your
choosing and explain it in the tool's docstring."* So every threshold below is
a named constant, every verdict reports the rule that produced it, and none of
this touches the network - which is what makes it all directly testable.

Two decisions worth stating outright.

**A verdict always carries its reason.** `{"needed": true}` tells a user
nothing they can argue with. `"73% chance of rain, above the 40% threshold"`
tells them what was measured, what the rule was, and lets them overrule it.
An agent relaying the first has to invent an explanation; relaying the second,
it only has to read one out.

**Missing data is `None`, never `False`.** Open-Meteo drops fields at the edge
of its forecast horizon - a 14-day request comes back with precipitation
probability for the first 10 days and nothing after. Treating that absence as
"no rain expected" produces a confident, cheerful, wrong answer, which is the
worst kind available here, because nothing about it looks wrong. So an
unavailable input yields a null verdict and a reason that says so, and the
agent's system prompt tells it to admit that rather than paper over it.
"""

from __future__ import annotations

from typing import Any

# -- Thresholds -------------------------------------------------------------
#
# Round numbers, chosen to be defensible and easy to explain rather than
# tuned. They are constants so that the docstrings, the tests, and the tool's
# own output can all cite the same value, and changing a rule is a one-line
# change in one place.

UMBRELLA_PROBABILITY_PCT = 40
"""At or above this chance of precipitation, take an umbrella. Set at 40
because below roughly this level most people would rather be occasionally
rained on than carry one all day."""

UMBRELLA_PRECIPITATION_MM = 1.0
"""Or if this much rain is expected regardless of the stated probability. A
low-probability day forecasting 20mm is a day it is going to rain somewhere,
hard."""

JACKET_HIGH_F = 60.0
"""Below this daytime high, take a jacket."""

JACKET_WIND_MPH = 15.0
JACKET_WINDY_HIGH_F = 70.0
"""Wind moves the line. 65F is pleasant; 65F in a 20mph wind is not, because
wind strips the boundary layer of warm air the body maintains against skin."""

SUNSCREEN_UV_INDEX = 6.0
"""WHO classifies UV 6-7 as "high" and recommends protection from 3 upward.
Set at 6 so the tool flags what a reasonable person would actually act on
rather than nagging on every clear day."""

HEAT_CAUTION_F = 95.0
HIGH_WIND_MPH = 25.0
COLD_CAUTION_F = 20.0
"""Above, or below, these an outdoor plan needs rethinking rather than a
different coat."""

_SEVERE_ALERT_SEVERITIES = ("Extreme", "Severe")
"""An active NWS alert at these levels overrides every number above. A human
forecaster naming a specific hazard for a specific county outranks a model's
daily aggregate, which is the entire reason for pulling in a second source."""

UNSAFE_WEATHER_CODES = {95, 96, 99}
"""WMO thunderstorm codes. These make a day unsafe outdoors on their own, no
matter how pleasant the temperature is. Caught late, during a check against
live data: a Chicago day forecasting "Thunderstorm with light hail" at 88F and
12mph wind crossed no heat, cold, or wind threshold, so the first version of
this module cheerfully called it good conditions for being outside. Lightning
does not appear in a daily aggregate of temperature and wind."""

CAUTION_WEATHER_CODES = {45, 48, 56, 57, 65, 66, 67, 75, 77, 82, 85, 86}
"""Fog, freezing rain, heavy rain or snow, violent showers. Not disqualifying,
but not something to find out about on arrival either."""


def _verdict(needed: bool | None, because: str) -> dict:
    """One recommendation: the answer, and the rule that produced it."""
    return {"needed": needed, "because": because}


def _f(value: Any) -> float | None:
    """Coerce to float, or None. Open-Meteo sends nulls in the gaps."""
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def umbrella(day: dict) -> dict:
    """Whether to take an umbrella.

    Rule: precipitation probability >= UMBRELLA_PROBABILITY_PCT (40%), or
    expected precipitation >= UMBRELLA_PRECIPITATION_MM (1.0 mm).
    """
    probability = _f(day.get("precipitation_probability_pct"))
    millimetres = _f(day.get("precipitation_mm"))

    if probability is None and millimetres is None:
        return _verdict(None, "No precipitation data is available for this day.")

    if probability is not None and probability >= UMBRELLA_PROBABILITY_PCT:
        return _verdict(
            True,
            f"{probability:.0f}% chance of precipitation, at or above the "
            f"{UMBRELLA_PROBABILITY_PCT}% threshold.",
        )

    if millimetres is not None and millimetres >= UMBRELLA_PRECIPITATION_MM:
        return _verdict(
            True,
            f"{millimetres:.1f} mm of precipitation expected, at or above the "
            f"{UMBRELLA_PRECIPITATION_MM} mm threshold"
            + (
                f", even though the stated chance is only {probability:.0f}%."
                if probability is not None
                else "."
            ),
        )

    stated = f"{probability:.0f}% chance" if probability is not None else "no stated chance"
    return _verdict(
        False,
        f"{stated} of precipitation, below the {UMBRELLA_PROBABILITY_PCT}% threshold.",
    )


def jacket(day: dict) -> dict:
    """Whether to take a jacket.

    Rule: daytime high < JACKET_HIGH_F (60F), or high < JACKET_WINDY_HIGH_F
    (70F) with wind above JACKET_WIND_MPH (15 mph). Wind is in the rule because
    it strips away the layer of warm air the body holds against skin, so a
    given temperature feels colder the harder it blows.
    """
    high = _f(day.get("high_f"))
    wind = _f(day.get("max_wind_mph"))

    if high is None:
        return _verdict(None, "No temperature data is available for this day.")

    if high < JACKET_HIGH_F:
        return _verdict(
            True, f"High of {high:.0f}F, below the {JACKET_HIGH_F:.0f}F threshold."
        )

    if wind is not None and wind > JACKET_WIND_MPH and high < JACKET_WINDY_HIGH_F:
        return _verdict(
            True,
            f"High of {high:.0f}F with wind to {wind:.0f} mph. Above "
            f"{JACKET_WIND_MPH:.0f} mph, anything under {JACKET_WINDY_HIGH_F:.0f}F "
            "feels colder than the number suggests.",
        )

    return _verdict(
        False, f"High of {high:.0f}F, at or above the {JACKET_HIGH_F:.0f}F threshold."
    )


def sunscreen(day: dict) -> dict:
    """Whether to wear sunscreen.

    Rule: maximum UV index >= SUNSCREEN_UV_INDEX (6). The WHO calls 6-7 "high".
    """
    uv = _f(day.get("uv_index_max"))
    if uv is None:
        return _verdict(None, "No UV index is available for this day.")
    if uv >= SUNSCREEN_UV_INDEX:
        return _verdict(
            True,
            f"UV index reaches {uv:.1f}, at or above the {SUNSCREEN_UV_INDEX:.0f} "
            "threshold the WHO classes as high.",
        )
    return _verdict(
        False, f"UV index peaks at {uv:.1f}, below the {SUNSCREEN_UV_INDEX:.0f} threshold."
    )


def outdoor_activity(day: dict, alerts: list[dict] | None = None) -> dict:
    """Whether outdoor plans are a good idea.

    Rule, worst first:
      1. An active NWS alert of Extreme or Severe severity. A named hazard from
         a human forecaster overrides every modelled number below it.
      2. A thunderstorm in the forecast (UNSAFE_WEATHER_CODES).
      3. High >= HEAT_CAUTION_F (95F).
      4. Wind >= HIGH_WIND_MPH (25 mph).
      5. High <= COLD_CAUTION_F (20F).
      6. Fog, freezing rain, or heavy precipitation (CAUTION_WEATHER_CODES).

    Returns a three-way `advice` - "avoid", "take care", or "good" - rather
    than a boolean, because "should I go outside" genuinely has a middle answer
    and collapsing it loses the case people most need warning about.
    """
    reasons: list[str] = []
    advice = "good"

    for alert in alerts or []:
        if str(alert.get("severity")) in _SEVERE_ALERT_SEVERITIES:
            advice = "avoid"
            reasons.append(
                f"{alert.get('severity')} weather alert in effect: "
                f"{alert.get('event')}"
                + (f" ({alert.get('area')})" if alert.get("area") else "")
                + "."
            )

    high = _f(day.get("high_f"))
    wind = _f(day.get("max_wind_mph"))
    code = day.get("weather_code")
    conditions = day.get("conditions") or "these conditions"

    if code in UNSAFE_WEATHER_CODES:
        advice = "avoid"
        reasons.append(f"{conditions} forecast. Lightning is not something to wait out outdoors.")
    elif code in CAUTION_WEATHER_CODES:
        advice = "avoid" if advice == "avoid" else "take care"
        reasons.append(f"{conditions} forecast.")

    if high is not None and high >= HEAT_CAUTION_F:
        advice = "avoid" if advice == "avoid" else "take care"
        reasons.append(
            f"High of {high:.0f}F, at or above the {HEAT_CAUTION_F:.0f}F heat threshold."
        )
    if high is not None and high <= COLD_CAUTION_F:
        advice = "avoid" if advice == "avoid" else "take care"
        reasons.append(
            f"High of only {high:.0f}F, at or below the {COLD_CAUTION_F:.0f}F cold threshold."
        )
    if wind is not None and wind >= HIGH_WIND_MPH:
        advice = "avoid" if advice == "avoid" else "take care"
        reasons.append(
            f"Wind to {wind:.0f} mph, at or above the {HIGH_WIND_MPH:.0f} mph threshold."
        )

    if not reasons:
        # No temperature means no heat or cold judgement, and a clear weather
        # code does not make up for that. "Unknown" beats a cheerful "good"
        # built on a field that was never there.
        if high is None:
            return {
                "advice": "unknown",
                "because": ["No temperature data is available for this day."],
            }
        reasons.append("No heat, cold, wind, or alert thresholds were crossed.")

    return {"advice": advice, "because": reasons}


def recommend(day: dict, alerts: list[dict] | None = None) -> dict:
    """Every recommendation for one forecast day.

    Args:
        day: One entry from WeatherProvider.daily_forecast()["days"].
        alerts: Active NWS alerts for the same place, if any. Only Extreme and
            Severe ones change a verdict.

    Returns:
        A dict of verdicts, each carrying `needed` and `because`, plus a
        one-line `summary` and the thresholds that were applied - so a reader
        can check the arithmetic without reading this file.
    """
    verdicts = {
        "umbrella": umbrella(day),
        "jacket": jacket(day),
        "sunscreen": sunscreen(day),
        "outdoor_activity": outdoor_activity(day, alerts),
    }

    return {
        "date": day.get("date"),
        "conditions": day.get("conditions"),
        "high_f": day.get("high_f"),
        "low_f": day.get("low_f"),
        **verdicts,
        "summary": _summarize(verdicts),
        "thresholds_applied": {
            "umbrella_probability_pct": UMBRELLA_PROBABILITY_PCT,
            "umbrella_precipitation_mm": UMBRELLA_PRECIPITATION_MM,
            "jacket_high_f": JACKET_HIGH_F,
            "jacket_wind_mph": JACKET_WIND_MPH,
            "jacket_windy_high_f": JACKET_WINDY_HIGH_F,
            "sunscreen_uv_index": SUNSCREEN_UV_INDEX,
            "heat_caution_f": HEAT_CAUTION_F,
            "high_wind_mph": HIGH_WIND_MPH,
            "cold_caution_f": COLD_CAUTION_F,
        },
    }


def _summarize(verdicts: dict) -> str:
    """One sentence an agent can relay without doing any reasoning of its own."""
    take = [name for name in ("umbrella", "jacket", "sunscreen") if verdicts[name]["needed"]]
    unknown = [
        name for name in ("umbrella", "jacket", "sunscreen") if verdicts[name]["needed"] is None
    ]

    parts: list[str] = []
    if take:
        parts.append("Take " + _join(take) + ".")
    elif not unknown:
        parts.append("No umbrella, jacket, or sunscreen needed.")

    if unknown:
        parts.append("No data to judge " + _join(unknown) + ".")

    advice = verdicts["outdoor_activity"]["advice"]
    parts.append(
        {
            "avoid": "Outdoor plans are not advised.",
            "take care": "Outdoor plans are workable with care.",
            "good": "Good conditions for being outside.",
            "unknown": "Not enough data to judge outdoor plans.",
        }[advice]
    )
    return " ".join(parts)


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


__all__ = [
    "CAUTION_WEATHER_CODES",
    "COLD_CAUTION_F",
    "HEAT_CAUTION_F",
    "HIGH_WIND_MPH",
    "JACKET_HIGH_F",
    "JACKET_WINDY_HIGH_F",
    "JACKET_WIND_MPH",
    "SUNSCREEN_UV_INDEX",
    "UMBRELLA_PRECIPITATION_MM",
    "UMBRELLA_PROBABILITY_PCT",
    "UNSAFE_WEATHER_CODES",
    "jacket",
    "outdoor_activity",
    "recommend",
    "sunscreen",
    "umbrella",
]
