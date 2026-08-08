"""The Open-Meteo adapter, against recorded real responses."""

from __future__ import annotations

import pytest
from conftest import FakeResponse, FakeSession, load_fixture
from http_client import JsonHttpClient
from location import LocationError
from weather_provider import (
    MAX_FORECAST_DAYS,
    LocationNotFound,
    WeatherProvider,
    WeatherServiceError,
    describe_bearing,
    describe_code,
)

GEOCODE = load_fixture("open_meteo_geocode_chicago.json")
FORECAST = load_fixture("open_meteo_forecast_chicago.json")
GEOCODE_EMPTY = load_fixture("open_meteo_geocode_empty.json")


def provider(responses):
    session = FakeSession(responses)
    http = JsonHttpClient(session=session, sleep=lambda _s: None, clock=lambda: 0.0)
    return WeatherProvider(http=http), session


class TestResolve:
    def test_city_and_state_are_geocoded(self):
        weather, session = provider([FakeResponse(GEOCODE)])
        place = weather.resolve("Chicago, IL")
        assert place.label == "Chicago, IL"
        assert (round(place.latitude), round(place.longitude)) == (42, -88)
        assert session.last_params["name"] == "Chicago"
        assert session.last_params["countryCode"] == "US"

    def test_coordinates_skip_the_geocoder_entirely(self):
        # No queued response: if this made a network call the FakeSession
        # would raise. That is the assertion.
        weather, session = provider([])
        place = weather.resolve("41.8781,-87.6298")
        assert (place.latitude, place.longitude) == (41.8781, -87.6298)
        assert session.calls == []

    def test_a_state_mismatch_is_not_accepted(self):
        # Open-Meteo ranks by population, so the first "Chicago" is the Illinois
        # one. Asking for Chicago, TX must not silently return Illinois weather.
        weather, _ = provider([FakeResponse(GEOCODE)])
        with pytest.raises(LocationNotFound, match="No US place found"):
            weather.resolve("Chicago, TX")

    def test_no_results_raises_location_not_found(self):
        weather, _ = provider([FakeResponse(GEOCODE_EMPTY)])
        with pytest.raises(LocationNotFound):
            weather.resolve("Zzzznotacity, IL")

    def test_a_malformed_location_never_reaches_the_network(self):
        weather, session = provider([])
        with pytest.raises(LocationError):
            weather.resolve("Springfield")
        assert session.calls == []

    def test_a_geocoder_outage_is_a_service_error_not_a_location_error(self):
        # These are different problems: one the user can fix by retyping, the
        # other they cannot. The agent's guardrails depend on telling them apart.
        weather, _ = provider([FakeResponse(status_code=500)] * 3)
        with pytest.raises(WeatherServiceError):
            weather.resolve("Chicago, IL")


class TestCurrentConditions:
    def test_returns_named_units(self):
        weather, _ = provider([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        now = weather.current_conditions("Chicago, IL")
        assert now["temperature_f"] == 78.8
        assert now["feels_like_f"] == 85.4
        assert now["humidity_pct"] == 67
        assert now["wind_mph"] == 5.3
        assert now["precipitation_mm"] == 0.0

    def test_translates_the_weather_code(self):
        weather, _ = provider([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        assert weather.current_conditions("Chicago, IL")["conditions"] == "Clear sky"

    def test_translates_the_wind_bearing(self):
        weather, _ = provider([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        # 327 degrees
        assert weather.current_conditions("Chicago, IL")["wind_direction"] == "NNW"

    def test_reports_local_time_not_utc(self):
        weather, session = provider([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        now = weather.current_conditions("Chicago, IL")
        assert now["timezone"] == "America/Chicago"
        assert session.last_params["timezone"] == "auto"

    def test_requests_fahrenheit_and_mph(self):
        weather, session = provider([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        weather.current_conditions("Chicago, IL")
        assert session.last_params["temperature_unit"] == "fahrenheit"
        assert session.last_params["wind_speed_unit"] == "mph"

    def test_a_response_with_no_current_block_is_an_error(self):
        weather, _ = provider([FakeResponse(GEOCODE), FakeResponse({"timezone": "UTC"})])
        with pytest.raises(WeatherServiceError, match="no current observation"):
            weather.current_conditions("Chicago, IL")


class TestDailyForecast:
    def test_returns_one_entry_per_day(self):
        weather, _ = provider([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        forecast = weather.daily_forecast("Chicago, IL", days=3)
        assert [day["date"] for day in forecast["days"]] == [
            "2026-08-08", "2026-08-09", "2026-08-10",
        ]

    def test_carries_the_fields_the_recommendation_needs(self):
        weather, _ = provider([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        monday = weather.daily_forecast("Chicago, IL", days=3)["days"][2]
        assert monday["high_f"] == 88.3
        assert monday["low_f"] == 68.9
        assert monday["precipitation_probability_pct"] == 73
        assert monday["max_wind_mph"] == 17.8
        assert monday["uv_index_max"] == 7.05
        assert monday["conditions"] == "Thunderstorm with light hail"

    def test_days_is_passed_upstream(self):
        weather, session = provider([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        weather.daily_forecast("Chicago, IL", days=3)
        assert session.last_params["forecast_days"] == 3

    def test_days_is_clamped_to_the_open_meteo_ceiling(self):
        # Asking for 30 is a 400 upstream. Clamping turns a confusing remote
        # failure into a slightly shorter forecast.
        weather, session = provider([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        weather.daily_forecast("Chicago, IL", days=30)
        assert session.last_params["forecast_days"] == MAX_FORECAST_DAYS

    def test_days_below_one_is_clamped_up(self):
        weather, session = provider([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        weather.daily_forecast("Chicago, IL", days=0)
        assert session.last_params["forecast_days"] == 1

    def test_a_short_column_pads_rather_than_truncating(self):
        # Fields drop out at the edge of the model horizon. A missing UV index
        # on day 14 must not silently shorten a 14-day forecast to 10.
        payload = {
            "timezone": "America/Chicago",
            "daily": {
                "time": ["2026-08-08", "2026-08-09", "2026-08-10"],
                "weather_code": [0, 1],
                "temperature_2m_max": [80.3],
                "uv_index_max": [],
            },
        }
        weather, _ = provider([FakeResponse(GEOCODE), FakeResponse(payload)])
        days = weather.daily_forecast("Chicago, IL", days=3)["days"]
        assert len(days) == 3
        assert days[2]["high_f"] is None
        assert days[2]["uv_index_max"] is None

    def test_an_empty_forecast_is_an_error(self):
        weather, _ = provider([FakeResponse(GEOCODE), FakeResponse({"daily": {"time": []}})])
        with pytest.raises(WeatherServiceError, match="no forecast"):
            weather.daily_forecast("Chicago, IL")


class TestTranslations:
    @pytest.mark.parametrize(
        ("code", "words"),
        [(0, "Clear sky"), (3, "Overcast"), (63, "Moderate rain"), (99, "Thunderstorm with heavy hail")],
    )
    def test_known_codes(self, code, words):
        assert describe_code(code) == words

    def test_an_unknown_code_reports_itself(self):
        # "Unknown" would hide which code is missing from the table.
        assert "77777" in describe_code(77777)

    def test_a_missing_code_does_not_crash(self):
        assert describe_code(None).startswith("Unrecognized")

    @pytest.mark.parametrize(
        ("degrees", "point"),
        [(0, "N"), (90, "E"), (180, "S"), (270, "W"), (327, "NNW"), (359, "N")],
    )
    def test_bearings(self, degrees, point):
        assert describe_bearing(degrees) == point

    def test_a_missing_bearing_is_none_not_a_guess(self):
        assert describe_bearing(None) is None
