"""The derived judgement. No network here, so every branch is directly testable
- which is the reason this logic lives in its own module."""

from __future__ import annotations

import pytest
from recommendation import (  # noqa: I001
    HEAT_CAUTION_F,
    HIGH_WIND_MPH,
    JACKET_HIGH_F,
    SUNSCREEN_UV_INDEX,
    UNSAFE_WEATHER_CODES,
    UMBRELLA_PRECIPITATION_MM,
    UMBRELLA_PROBABILITY_PCT,
    jacket,
    outdoor_activity,
    recommend,
    sunscreen,
    umbrella,
)


def day(**overrides) -> dict:
    """A mild, unremarkable day. Tests move one dial at a time from here."""
    base = {
        "date": "2026-08-08",
        "conditions": "Mainly clear",
        "weather_code": 1,
        "high_f": 75.0,
        "low_f": 60.0,
        "precipitation_probability_pct": 5,
        "precipitation_mm": 0.0,
        "max_wind_mph": 8.0,
        "uv_index_max": 4.0,
    }
    base.update(overrides)
    return base


class TestUmbrella:
    def test_above_the_probability_threshold(self):
        verdict = umbrella(day(precipitation_probability_pct=73))
        assert verdict["needed"] is True
        assert "73%" in verdict["because"]

    def test_exactly_at_the_threshold_counts(self):
        assert umbrella(day(precipitation_probability_pct=UMBRELLA_PROBABILITY_PCT))["needed"]

    def test_one_below_the_threshold_does_not(self):
        assert (
            umbrella(day(precipitation_probability_pct=UMBRELLA_PROBABILITY_PCT - 1))["needed"]
            is False
        )

    def test_heavy_rain_overrides_a_low_probability(self):
        # A 10% day forecasting 20mm is a day it is going to rain somewhere,
        # hard. The probability alone would wave that through.
        verdict = umbrella(day(precipitation_probability_pct=10, precipitation_mm=20.0))
        assert verdict["needed"] is True
        assert "20.0 mm" in verdict["because"]
        assert "only 10%" in verdict["because"]

    def test_the_millimetre_threshold_is_inclusive(self):
        assert umbrella(
            day(precipitation_probability_pct=0, precipitation_mm=UMBRELLA_PRECIPITATION_MM)
        )["needed"]

    def test_missing_data_is_none_not_false(self):
        # The whole point. A confident "no umbrella" built on absent data is a
        # wrong answer that looks exactly like a right one.
        verdict = umbrella(day(precipitation_probability_pct=None, precipitation_mm=None))
        assert verdict["needed"] is None
        assert "No precipitation data" in verdict["because"]

    def test_the_reason_always_cites_the_threshold(self):
        for probability in (0, 39, 40, 100):
            assert str(UMBRELLA_PROBABILITY_PCT) in umbrella(
                day(precipitation_probability_pct=probability)
            )["because"]


class TestJacket:
    def test_cold_day(self):
        verdict = jacket(day(high_f=48.0))
        assert verdict["needed"] is True
        assert "48F" in verdict["because"]

    def test_warm_day(self):
        assert jacket(day(high_f=82.0))["needed"] is False

    def test_wind_moves_the_line(self):
        # 65F is pleasant. 65F in a 22mph wind is not, and a bare temperature
        # threshold cannot see the difference.
        verdict = jacket(day(high_f=65.0, max_wind_mph=22.0))
        assert verdict["needed"] is True
        assert "22 mph" in verdict["because"]

    def test_wind_does_not_apply_on_a_hot_day(self):
        assert jacket(day(high_f=88.0, max_wind_mph=30.0))["needed"] is False

    def test_calm_and_mild_needs_none(self):
        assert jacket(day(high_f=65.0, max_wind_mph=5.0))["needed"] is False

    def test_boundary_is_strictly_below(self):
        assert jacket(day(high_f=JACKET_HIGH_F))["needed"] is False
        assert jacket(day(high_f=JACKET_HIGH_F - 0.1))["needed"] is True

    def test_missing_temperature_is_none(self):
        assert jacket(day(high_f=None))["needed"] is None


class TestSunscreen:
    def test_high_uv(self):
        verdict = sunscreen(day(uv_index_max=7.15))
        assert verdict["needed"] is True
        assert "7.1" in verdict["because"] or "7.2" in verdict["because"]

    def test_low_uv(self):
        assert sunscreen(day(uv_index_max=3.75))["needed"] is False

    def test_the_threshold_is_inclusive(self):
        assert sunscreen(day(uv_index_max=SUNSCREEN_UV_INDEX))["needed"] is True

    def test_missing_uv_is_none(self):
        assert sunscreen(day(uv_index_max=None))["needed"] is None


class TestOutdoorActivity:
    def test_a_pleasant_day_is_good(self):
        result = outdoor_activity(day())
        assert result["advice"] == "good"

    def test_heat_downgrades_to_take_care(self):
        result = outdoor_activity(day(high_f=HEAT_CAUTION_F + 3))
        assert result["advice"] == "take care"
        assert any("heat threshold" in reason for reason in result["because"])

    def test_wind_downgrades_to_take_care(self):
        result = outdoor_activity(day(max_wind_mph=HIGH_WIND_MPH + 5))
        assert result["advice"] == "take care"

    def test_cold_downgrades_to_take_care(self):
        result = outdoor_activity(day(high_f=12.0))
        assert result["advice"] == "take care"

    def test_a_severe_alert_overrides_a_perfect_forecast(self):
        # The entire reason for pulling in a second source. Open-Meteo cannot
        # know a human forecaster has issued a tornado warning for this county.
        alerts = [{"severity": "Severe", "event": "Tornado Warning", "area": "Cook, IL"}]
        result = outdoor_activity(day(), alerts)
        assert result["advice"] == "avoid"
        assert any("Tornado Warning" in reason for reason in result["because"])

    def test_a_minor_alert_does_not_override(self):
        # Every Minor advisory grounding every plan would make the tool useless
        # on any day with weather in it.
        alerts = [{"severity": "Minor", "event": "Flood Advisory", "area": "Red River, TX"}]
        assert outdoor_activity(day(), alerts)["advice"] == "good"

    def test_avoid_is_not_downgraded_by_a_lesser_reason(self):
        alerts = [{"severity": "Extreme", "event": "Hurricane Warning", "area": "Miami-Dade, FL"}]
        result = outdoor_activity(day(high_f=HEAT_CAUTION_F + 1), alerts)
        assert result["advice"] == "avoid"
        assert len(result["because"]) == 2

    def test_a_thunderstorm_is_avoid_however_pleasant_the_numbers_are(self):
        # Caught against live data, not by a test written up front. A Chicago
        # day forecasting "Thunderstorm with light hail" at 88F in a 12mph wind
        # crosses no heat, cold, or wind threshold, and the first version of
        # this module called it good conditions for being outside. Lightning
        # does not show up in a daily aggregate of temperature and wind.
        result = outdoor_activity(day(weather_code=96, conditions="Thunderstorm with light hail"))
        assert result["advice"] == "avoid"
        assert any("Lightning" in reason for reason in result["because"])

    @pytest.mark.parametrize("code", sorted(UNSAFE_WEATHER_CODES))
    def test_every_thunderstorm_code_is_avoid(self, code):
        assert outdoor_activity(day(weather_code=code))["advice"] == "avoid"

    @pytest.mark.parametrize("code", [45, 48, 65, 67, 75, 82, 86])
    def test_hazardous_but_survivable_conditions_are_take_care(self, code):
        result = outdoor_activity(day(weather_code=code, conditions="Fog"))
        assert result["advice"] == "take care"

    def test_ordinary_rain_is_not_a_reason_to_stay_in(self):
        # 61 is light rain. Downgrading every wet day would make the tool
        # useless in most of the country for most of the year.
        assert outdoor_activity(day(weather_code=61))["advice"] == "good"

    def test_the_code_is_read_not_the_english(self):
        # Keying off day["conditions"] text would mean editing a phrase in
        # WMO_CODES could silently change a safety verdict.
        assert outdoor_activity(day(weather_code=95, conditions="Lovely day"))["advice"] == "avoid"

    def test_reasons_are_given_even_when_nothing_is_wrong(self):
        assert outdoor_activity(day())["because"]

    def test_missing_temperature_is_unknown_not_good(self):
        result = outdoor_activity(day(high_f=None, max_wind_mph=None))
        assert result["advice"] == "unknown"


class TestRecommend:
    def test_bundles_every_verdict(self):
        result = recommend(day())
        assert set(result) >= {
            "date", "conditions", "high_f", "low_f", "umbrella", "jacket",
            "sunscreen", "outdoor_activity", "summary", "thresholds_applied",
        }

    def test_publishes_the_thresholds_it_used(self):
        # So a reader can check the arithmetic without opening the source.
        thresholds = recommend(day())["thresholds_applied"]
        assert thresholds["umbrella_probability_pct"] == UMBRELLA_PROBABILITY_PCT
        assert thresholds["sunscreen_uv_index"] == SUNSCREEN_UV_INDEX

    def test_summary_lists_what_to_take(self):
        summary = recommend(day(precipitation_probability_pct=80, uv_index_max=9.0))["summary"]
        assert "umbrella" in summary
        assert "sunscreen" in summary

    def test_summary_on_a_clear_mild_day(self):
        assert "No umbrella, jacket, or sunscreen needed." in recommend(day())["summary"]

    def test_summary_admits_what_it_could_not_judge(self):
        summary = recommend(day(uv_index_max=None))["summary"]
        assert "No data to judge sunscreen." in summary

    def test_summary_carries_the_alert_verdict(self):
        alerts = [{"severity": "Severe", "event": "Tornado Warning", "area": "Cook, IL"}]
        assert "not advised" in recommend(day(), alerts)["summary"]

    @pytest.mark.parametrize("field", ["high_f", "precipitation_probability_pct", "uv_index_max"])
    def test_any_single_missing_field_does_not_crash(self, field):
        assert recommend(day(**{field: None}))["summary"]

    def test_a_completely_empty_day_does_not_crash(self):
        # Defensive: the forecast horizon can drop everything at once.
        assert recommend({})["summary"]
