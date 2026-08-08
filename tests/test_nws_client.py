"""The NWS alerts adapter, against a recorded real statewide response."""

from __future__ import annotations

import pytest
from conftest import FakeResponse, FakeSession, load_fixture
from http_client import JsonHttpClient
from nws_client import AlertServiceError, NWSAlertClient, severity_rank

ALERTS_TX = load_fixture("nws_alerts_tx.json")
ALERTS_NONE = load_fixture("nws_alerts_none.json")

USER_AGENT = "(SkyCast-AI, test@example.com)"


def alert_client(responses):
    session = FakeSession(responses)
    http = JsonHttpClient(session=session, sleep=lambda _s: None, clock=lambda: 0.0)
    return NWSAlertClient(user_agent=USER_AGENT, http=http), session


class TestUserAgent:
    def test_a_missing_user_agent_fails_loudly_at_construction(self):
        # Better here than as a 403 on the first live call, which reads like
        # an outage rather than a configuration mistake.
        with pytest.raises(ValueError, match="User-Agent"):
            NWSAlertClient(user_agent="")

    def test_a_blank_user_agent_is_also_rejected(self):
        with pytest.raises(ValueError):
            NWSAlertClient(user_agent="   ")

    def test_it_is_sent_on_the_session(self):
        client = NWSAlertClient(user_agent=USER_AGENT, session=FakeSession())
        assert client.http.session.headers["User-Agent"] == USER_AGENT
        assert client.http.session.headers["Accept"] == "application/geo+json"


class TestQuery:
    def test_queries_by_point_not_by_state(self):
        # A statewide Texas query returns Louisiana-border advisories to a user
        # in El Paso. Technically an answer, practically noise.
        client, session = alert_client([FakeResponse(ALERTS_NONE)])
        client.active_alerts(41.85, -87.65)
        assert session.last_params == {"point": "41.85,-87.65"}
        assert session.urls[-1].endswith("/alerts/active")

    def test_no_limit_parameter_is_ever_sent(self):
        # /alerts/active rejects `limit` with a 400 whose body has no
        # "features" key - so lenient parsing reports "no alerts" for a state
        # that has thirty. Capping happens after parsing, deliberately.
        client, session = alert_client([FakeResponse(ALERTS_TX)])
        client.active_alerts(31.0, -97.0, limit=2)
        assert "limit" not in session.last_params

    def test_no_active_alerts_is_an_empty_list_not_an_error(self):
        client, _ = alert_client([FakeResponse(ALERTS_NONE)])
        assert client.active_alerts(41.85, -87.65) == []


class TestNormalization:
    def test_flattens_the_geojson_feature(self):
        client, _ = alert_client([FakeResponse(ALERTS_TX)])
        alert = client.active_alerts(31.0, -97.0)[0]
        assert set(alert) == {
            "id", "event", "severity", "urgency", "certainty", "headline",
            "area", "description", "instruction", "effective", "expires",
            "issued_by",
        }
        assert alert["event"]
        assert alert["id"]

    def test_description_and_instruction_stay_separate(self):
        # They answer different questions: what is happening, and what to do
        # about it. An agent deciding whether to warn someone wants the second.
        client, _ = alert_client([FakeResponse(ALERTS_TX)])
        alerts = client.active_alerts(31.0, -97.0, limit=20)
        assert any(alert["description"] for alert in alerts)

    def test_sorted_worst_first(self):
        client, _ = alert_client([FakeResponse(ALERTS_TX)])
        alerts = client.active_alerts(31.0, -97.0, limit=20)
        ranks = [severity_rank(alert["severity"]) for alert in alerts]
        assert ranks == sorted(ranks)

    def test_limit_caps_the_result(self):
        client, _ = alert_client([FakeResponse(ALERTS_TX)])
        assert len(client.active_alerts(31.0, -97.0, limit=2)) == 2

    def test_a_feature_with_no_event_is_dropped(self):
        client, _ = alert_client(
            [FakeResponse({"features": [{"id": "x", "properties": {"id": "x"}}]})]
        )
        assert client.active_alerts(31.0, -97.0) == []

    def test_missing_severity_becomes_unknown_and_sorts_last(self):
        client, _ = alert_client(
            [
                FakeResponse(
                    {
                        "features": [
                            {"properties": {"id": "a", "event": "Test", "severity": None}},
                            {"properties": {"id": "b", "event": "Test", "severity": "Severe"}},
                        ]
                    }
                )
            ]
        )
        alerts = client.active_alerts(31.0, -97.0)
        assert [alert["severity"] for alert in alerts] == ["Severe", "Unknown"]


class TestFailures:
    def test_an_outage_becomes_a_readable_error(self):
        client, _ = alert_client([FakeResponse(status_code=500)] * 3)
        with pytest.raises(AlertServiceError, match="Could not reach the alert service"):
            client.active_alerts(41.85, -87.65)

    def test_a_403_names_the_problem(self):
        client, _ = alert_client(
            [FakeResponse({"detail": "User-Agent header is required"}, status_code=403)]
        )
        with pytest.raises(AlertServiceError, match="User-Agent"):
            client.active_alerts(41.85, -87.65)


class TestSeverityRank:
    def test_extreme_outranks_minor(self):
        assert severity_rank("Extreme") < severity_rank("Minor")

    def test_an_unrecognized_severity_sorts_last(self):
        assert severity_rank("Bananas") > severity_rank("Unknown")
