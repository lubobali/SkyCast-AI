"""The shared HTTP layer: retries, pacing, and errors a human can act on."""

from __future__ import annotations

import pytest
import requests
from conftest import FakeResponse, FakeSession
from http_client import HttpError, JsonHttpClient, Pacer


def client(session, **kwargs):
    kwargs.setdefault("sleep", lambda _s: None)
    kwargs.setdefault("clock", lambda: 0.0)
    return JsonHttpClient(session=session, **kwargs)


class TestSuccess:
    def test_returns_the_decoded_body(self):
        session = FakeSession([FakeResponse({"ok": True})])
        assert client(session).get_json("https://example.test/x") == {"ok": True}

    def test_passes_params_through(self):
        session = FakeSession([FakeResponse({})])
        client(session).get_json("https://example.test/x", {"latitude": 41.88})
        assert session.last_params == {"latitude": 41.88}

    def test_headers_are_set_on_the_session(self):
        session = FakeSession([FakeResponse({})])
        client(session, headers={"User-Agent": "(SkyCast-AI, me@example.com)"})
        assert session.headers["User-Agent"] == "(SkyCast-AI, me@example.com)"


class TestRetries:
    def test_a_500_is_retried_and_can_succeed(self):
        session = FakeSession([FakeResponse(status_code=503), FakeResponse({"ok": 1})])
        assert client(session).get_json("https://example.test/x") == {"ok": 1}
        assert len(session.calls) == 2

    def test_a_transport_error_is_retried(self):
        session = FakeSession(
            [requests.ConnectionError("connection reset"), FakeResponse({"ok": 1})]
        )
        assert client(session).get_json("https://example.test/x") == {"ok": 1}

    def test_retries_are_bounded(self):
        session = FakeSession([FakeResponse(status_code=500)] * 5)
        with pytest.raises(HttpError, match="did not respond after 3 attempts"):
            client(session).get_json("https://example.test/x")
        assert len(session.calls) == 3

    def test_backoff_is_exponential(self):
        slept: list[float] = []
        session = FakeSession([FakeResponse(status_code=500)] * 3)
        with pytest.raises(HttpError):
            client(session, backoff=0.5, sleep=slept.append).get_json("https://example.test/x")
        # Two waits for three attempts, doubling. No wait after the last one:
        # sleeping before giving up only makes the caller wait to hear "no".
        assert slept == [0.5, 1.0]

    def test_a_4xx_is_not_retried(self):
        # A 400 is a statement about the request. Repeating it cannot change
        # the answer, and on a 429 it actively makes things worse.
        session = FakeSession([FakeResponse({"reason": "bad latitude"}, status_code=400)])
        with pytest.raises(HttpError):
            client(session).get_json("https://example.test/x")
        assert len(session.calls) == 1


class TestErrorMessages:
    def test_open_meteo_reason_is_surfaced(self):
        session = FakeSession(
            [FakeResponse({"reason": "Latitude must be in range of -90 to 90"}, status_code=400)]
        )
        with pytest.raises(HttpError, match="Latitude must be in range"):
            client(session).get_json("https://example.test/x")

    def test_nws_problem_detail_is_surfaced(self):
        # api.weather.gov answers RFC 7807. Without reading the body, all that
        # escapes is "400 Client Error", which names nothing.
        session = FakeSession(
            [
                FakeResponse(
                    {
                        "title": "Invalid Parameter",
                        "detail": "Parameter 'limit' is not valid",
                        "parameterErrors": [
                            {"parameter": "limit", "message": "should NOT have limit"}
                        ],
                    },
                    status_code=400,
                )
            ]
        )
        with pytest.raises(HttpError) as caught:
            client(session).get_json("https://example.test/x")
        message = str(caught.value)
        assert "Invalid Parameter" in message
        assert "limit: should NOT have limit" in message

    def test_a_non_json_error_body_still_gives_the_status(self):
        session = FakeSession([FakeResponse(None, status_code=403, text="<html>nope</html>")])
        with pytest.raises(HttpError, match="HTTP 403"):
            client(session).get_json("https://example.test/x")

    def test_the_message_is_never_a_traceback(self):
        session = FakeSession([FakeResponse(status_code=500)] * 3)
        with pytest.raises(HttpError) as caught:
            client(session).get_json("https://example.test/x")
        assert "Traceback" not in str(caught.value)


class TestPacer:
    def test_zero_rate_never_waits(self):
        slept: list[float] = []
        pacer = Pacer(0, sleep=slept.append, clock=lambda: 0.0)
        pacer.wait()
        pacer.wait()
        assert slept == []

    def test_spaces_calls_to_the_configured_rate(self, fake_clock):
        state, clock, sleep = fake_clock
        pacer = Pacer(4, sleep=sleep, clock=clock)  # 4/second -> 0.25s apart
        pacer.wait()
        pacer.wait()
        assert state["slept"] == [0.25]

    def test_no_wait_when_enough_time_has_already_passed(self, fake_clock):
        state, clock, sleep = fake_clock
        pacer = Pacer(4, sleep=sleep, clock=clock)
        pacer.wait()
        state["now"] += 10.0
        pacer.wait()
        assert state["slept"] == []
