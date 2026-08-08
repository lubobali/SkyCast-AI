"""The MCP tool layer.

Two things are being proved here, and only these two, because everything else
is already covered where it lives:

  1. The tools are registered, named, and described well enough that an agent
     introspecting the server can pick the right one.
  2. **No tool ever raises.** A raise reaches the agent as a transport failure
     it can only report as "the tool broke". A returned `{"error": ...}` is a
     sentence it can act on - and, critically, one that tells it not to invent
     a forecast to fill the gap.
"""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("NWS_USER_AGENT", "(SkyCast-AI, test@example.com)")

import weather_mcp_server as server
from conftest import FakeResponse, FakeSession, load_fixture
from http_client import JsonHttpClient
from nws_client import NWSAlertClient
from weather_provider import WeatherProvider

GEOCODE = load_fixture("open_meteo_geocode_chicago.json")
FORECAST = load_fixture("open_meteo_forecast_chicago.json")
ALERTS_TX = load_fixture("nws_alerts_tx.json")
ALERTS_NONE = load_fixture("nws_alerts_none.json")

TOOL_NAMES = {
    "get_current_weather",
    "get_forecast",
    "get_outdoor_recommendation",
    "get_severe_weather_alerts",
    "compare_cities",
}


@pytest.fixture(autouse=True)
def isolated_adapters(monkeypatch):
    """Point both adapters at fake sessions the test controls.

    Reset around every test so a queued response can never leak between them.
    """
    state: dict = {}

    def wire(weather_responses, alert_responses=()):
        weather_session = FakeSession(list(weather_responses))
        alert_session = FakeSession(list(alert_responses))
        provider = WeatherProvider(
            http=JsonHttpClient(session=weather_session, sleep=lambda _s: None, clock=lambda: 0.0)
        )
        alerts = NWSAlertClient(
            user_agent="(SkyCast-AI, test@example.com)",
            http=JsonHttpClient(session=alert_session, sleep=lambda _s: None, clock=lambda: 0.0),
        )
        monkeypatch.setattr(server, "get_provider", lambda: provider)
        monkeypatch.setattr(server, "get_alert_client", lambda: alerts)
        state["weather"] = weather_session
        state["alerts"] = alert_session

    state["wire"] = wire
    monkeypatch.setattr(server, "_provider", None)
    monkeypatch.setattr(server, "_alerts", None)
    return state


def registered_tools() -> dict:
    """What the server advertises over MCP, keyed by name.

    Read through mcp.list_tools() rather than off the module, because what the
    agent discovers is the registry - not whatever functions happen to be
    defined in the file.
    """
    return {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}


class TestRegistration:
    def test_all_five_tools_are_registered(self):
        assert set(registered_tools()) == TOOL_NAMES

    def test_every_tool_documents_args_and_returns_in_source(self):
        # The Google-style docstring the homework asks for, for human readers
        # and for anyone grading the source.
        for name in TOOL_NAMES:
            docstring = getattr(server, name).__doc__ or ""
            assert "Args:" in docstring, f"{name} does not document its arguments"
            assert "Returns:" in docstring, f"{name} does not document its return"

    def test_every_argument_reaches_the_agent_as_a_schema_description(self):
        # FastMCP lifts the Args: block out of the docstring and into the JSON
        # schema, one description per parameter. That is what the agent
        # actually reads when deciding how to call a tool, so an undocumented
        # argument is an argument the agent will guess at.
        for name, tool in registered_tools().items():
            for argument, schema in (tool.parameters or {}).get("properties", {}).items():
                assert schema.get("description"), f"{name}.{argument} has no description"

    def test_the_agent_facing_description_survives_the_args_block(self):
        # The counterpart to the above, and the reason every docstring here is
        # laid out the way it is. FastMCP keeps only the text ABOVE "Args:" as
        # the tool description and discards the Returns: block entirely. So
        # anything the agent must know - null means unknown, an outage is not
        # an all-clear, do not guess - has to be stated before that line, or it
        # never reaches the agent at all.
        for name, tool in registered_tools().items():
            description = tool.description or ""
            assert "Args:" not in description
            assert len(description) > 200, f"{name} tells the agent almost nothing"

    def test_the_agent_is_told_that_null_is_not_zero(self):
        tools = registered_tools()
        assert "Null means unknown" in tools["get_forecast"].description
        assert 'Null is not "no"' in tools["get_outdoor_recommendation"].description

    def test_the_agent_is_told_not_to_fill_gaps_itself(self):
        assert "Do not substitute your own knowledge" in (
            registered_tools()["get_current_weather"].description
        )

    def test_the_prediction_tool_states_its_thresholds(self):
        # The homework asks for exactly this: the rule has to be in the
        # docstring, not buried in the code.
        description = registered_tools()["get_outdoor_recommendation"].description
        assert "40%" in description
        assert "60F" in description
        assert "UV index >= 6" in description

    def test_every_tool_declares_its_location_argument(self):
        for name, tool in registered_tools().items():
            properties = (tool.parameters or {}).get("properties", {})
            assert "location" in properties or "locations" in properties, name


class TestCurrentWeather:
    def test_happy_path(self, isolated_adapters):
        isolated_adapters["wire"]([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        result = server.get_current_weather("Chicago, IL")
        assert result["temperature_f"] == 78.8
        assert result["conditions"] == "Clear sky"
        assert "error" not in result

    def test_a_bad_location_is_returned_not_raised(self, isolated_adapters):
        isolated_adapters["wire"]([])
        result = server.get_current_weather("Springfield")
        assert result["error_type"] == "bad_request"
        assert "City, ST" in result["error"]

    def test_an_unknown_place_is_not_found(self, isolated_adapters):
        isolated_adapters["wire"]([FakeResponse({"results": []})])
        assert server.get_current_weather("Zzz, IL")["error_type"] == "not_found"

    def test_an_outage_is_a_service_error(self, isolated_adapters):
        isolated_adapters["wire"]([FakeResponse(status_code=500)] * 3)
        result = server.get_current_weather("Chicago, IL")
        assert result["error_type"] == "service_error"
        # No weather data alongside an error. A partial result is worse than
        # none: the agent reports whichever half it noticed.
        assert "temperature_f" not in result

    def test_a_non_string_argument_does_not_raise(self, isolated_adapters):
        isolated_adapters["wire"]([])
        assert "error" in server.get_current_weather(None)


class TestForecast:
    def test_happy_path(self, isolated_adapters):
        isolated_adapters["wire"]([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        result = server.get_forecast("Chicago, IL", days=3)
        assert len(result["days"]) == 3
        assert result["days"][0]["date"] == "2026-08-08"

    def test_days_is_clamped_not_rejected(self, isolated_adapters):
        isolated_adapters["wire"]([FakeResponse(GEOCODE), FakeResponse(FORECAST)])
        server.get_forecast("Chicago, IL", days=999)
        assert isolated_adapters["weather"].last_params["forecast_days"] == 16

    def test_a_word_for_days_is_an_error_not_a_crash(self, isolated_adapters):
        isolated_adapters["wire"]([])
        result = server.get_forecast("Chicago, IL", days="three")
        assert result["error_type"] == "bad_request"


class TestOutdoorRecommendation:
    def test_returns_verdicts_with_reasons(self, isolated_adapters):
        isolated_adapters["wire"](
            [FakeResponse(GEOCODE), FakeResponse(FORECAST)], [FakeResponse(ALERTS_NONE)]
        )
        result = server.get_outdoor_recommendation("Chicago, IL")
        assert result["umbrella"]["because"]
        assert result["thresholds_applied"]["umbrella_probability_pct"] == 40
        assert result["summary"]

    def test_picks_the_requested_date(self, isolated_adapters):
        isolated_adapters["wire"](
            [FakeResponse(GEOCODE), FakeResponse(FORECAST)], [FakeResponse(ALERTS_NONE)]
        )
        result = server.get_outdoor_recommendation("Chicago, IL", date="2026-08-10")
        assert result["date"] == "2026-08-10"
        # The recorded Monday is a hail thunderstorm.
        assert result["outdoor_activity"]["advice"] == "avoid"

    def test_a_date_outside_the_horizon_names_the_range(self, isolated_adapters):
        isolated_adapters["wire"](
            [FakeResponse(GEOCODE), FakeResponse(FORECAST)], [FakeResponse(ALERTS_NONE)]
        )
        result = server.get_outdoor_recommendation("Chicago, IL", date="2030-01-01")
        assert result["error_type"] == "bad_request"
        assert "2026-08-08 to 2026-08-10" in result["error"]

    def test_relative_dates_are_refused_with_advice(self, isolated_adapters):
        isolated_adapters["wire"]([])
        result = server.get_outdoor_recommendation("Chicago, IL", date="tomorrow")
        assert "get_forecast" in result["error"]

    def test_a_severe_alert_reaches_the_verdict(self, isolated_adapters):
        isolated_adapters["wire"](
            [FakeResponse(GEOCODE), FakeResponse(FORECAST)],
            [
                FakeResponse(
                    {
                        "features": [
                            {
                                "properties": {
                                    "id": "urn:x:1",
                                    "event": "Tornado Warning",
                                    "severity": "Severe",
                                    "areaDesc": "Cook, IL",
                                    "headline": "Tornado Warning issued",
                                }
                            }
                        ]
                    }
                )
            ],
        )
        result = server.get_outdoor_recommendation("Chicago, IL")
        assert result["outdoor_activity"]["advice"] == "avoid"
        assert result["active_alerts"][0]["event"] == "Tornado Warning"

    def test_an_alert_outage_degrades_rather_than_failing(self, isolated_adapters):
        # A 502 from NWS must not turn "should I take a jacket" into an error.
        # It must also not silently become "no alerts", which reads as an
        # all-clear, so the warning travels with the answer.
        isolated_adapters["wire"](
            [FakeResponse(GEOCODE), FakeResponse(FORECAST)],
            [FakeResponse(status_code=500)] * 3,
        )
        result = server.get_outdoor_recommendation("Chicago, IL")
        assert "error" not in result
        assert result["active_alerts"] == []
        assert "could not be checked" in result["warning"]


class TestSevereWeatherAlerts:
    def test_returns_alerts_worst_first(self, isolated_adapters):
        isolated_adapters["wire"]([FakeResponse(GEOCODE)], [FakeResponse(ALERTS_TX)])
        result = server.get_severe_weather_alerts("Chicago, IL")
        assert result["count"] == len(result["alerts"])
        assert result["alerts"]

    def test_no_alerts_is_a_normal_answer(self, isolated_adapters):
        isolated_adapters["wire"]([FakeResponse(GEOCODE)], [FakeResponse(ALERTS_NONE)])
        result = server.get_severe_weather_alerts("Chicago, IL")
        assert result == {"location": "Chicago, IL", "count": 0, "alerts": []}

    def test_an_outage_is_an_error_here_not_a_silent_all_clear(self, isolated_adapters):
        # Different from the recommendation tool on purpose. There, alerts are
        # supporting evidence. Here they are the entire question, and answering
        # "no alerts" when the service is down is the dangerous answer.
        isolated_adapters["wire"]([FakeResponse(GEOCODE)], [FakeResponse(status_code=500)] * 3)
        result = server.get_severe_weather_alerts("Chicago, IL")
        assert result["error_type"] == "service_error"
        assert "alerts" not in result


class TestCompareCities:
    def test_compares_several_places(self, isolated_adapters):
        # Coordinates, so both legs need only a forecast. The recorded geocode
        # fixture answers for Chicago alone, and feeding it a second city name
        # would test the state-mismatch guard rather than the comparison.
        isolated_adapters["wire"](
            [FakeResponse(FORECAST), FakeResponse(FORECAST)],
            [FakeResponse(ALERTS_NONE)] * 2,
        )
        result = server.compare_cities(["41.88,-87.63", "30.27,-97.74"])
        assert result["compared"] == 2
        assert result["errors"] == []
        assert [entry["location"] for entry in result["comparison"]] == [
            "41.88,-87.63", "30.27,-97.74",
        ]

    def test_coordinates_skip_the_geocode_for_that_leg(self, isolated_adapters):
        # A coordinate pair costs one request, a city name costs two. Queuing
        # for the wrong one is how a comparison silently loses a city.
        isolated_adapters["wire"](
            [FakeResponse(GEOCODE), FakeResponse(FORECAST), FakeResponse(FORECAST)],
            [FakeResponse(ALERTS_NONE)] * 2,
        )
        result = server.compare_cities(["Chicago, IL", "41.88,-87.63"])
        assert result["compared"] == 2
        assert len(isolated_adapters["weather"].calls) == 3

    def test_one_bad_city_does_not_lose_the_rest(self, isolated_adapters):
        # A trip planner that returns nothing because one city was misspelled
        # is worse than useless.
        isolated_adapters["wire"](
            [FakeResponse(GEOCODE), FakeResponse(FORECAST)], [FakeResponse(ALERTS_NONE)]
        )
        result = server.compare_cities(["Chicago, IL", "Nowhere"])
        assert result["compared"] == 1
        assert result["errors"][0]["location"] == "Nowhere"

    def test_one_place_is_refused_with_a_pointer(self, isolated_adapters):
        isolated_adapters["wire"]([])
        result = server.compare_cities(["Chicago, IL"])
        assert "get_forecast" in result["error"]


class TestNothingEverRaises:
    """The guardrail the agent's honesty depends on."""

    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            ("get_current_weather", (None,)),
            ("get_current_weather", ({"nested": "dict"},)),
            ("get_forecast", ("", "many")),
            ("get_outdoor_recommendation", (42, "not-a-date")),
            ("get_severe_weather_alerts", (None, "all")),
            ("compare_cities", (None, None)),
            ("compare_cities", (["", ""], None)),
        ],
    )
    def test_garbage_in_error_out(self, isolated_adapters, tool, args):
        isolated_adapters["wire"]([])
        result = getattr(server, tool)(*args)
        assert isinstance(result, dict)
        assert "error" in result

    def test_an_unexpected_internal_error_still_returns_a_dict(
        self, isolated_adapters, monkeypatch
    ):
        def explode():
            raise RuntimeError("something nobody predicted")

        monkeypatch.setattr(server, "get_provider", explode)
        result = server.get_current_weather("Chicago, IL")
        assert result["error_type"] == "internal_error"
        # The message has to tell the agent not to fill the gap itself.
        assert "do not guess" in result["error"]
        assert "something nobody predicted" not in result["error"]
