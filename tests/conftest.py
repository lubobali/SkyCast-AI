"""Shared test scaffolding.

The fake session below is the only thing standing between the tests and the
real internet. Everything under `tests/fixtures/` is a genuine recorded
response, captured 2026-08-08 with curl - not hand-written JSON. A test that
asserts against an invented response shape proves only that the author's
imagination is self-consistent with itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Read a recorded API response."""
    return json.loads((FIXTURES / name).read_text())


class FakeResponse:
    """Enough of requests.Response for the client under test."""

    def __init__(self, payload=None, status_code: int = 200, text: str | None = None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: not JSON")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Error", response=self
            )


class FakeSession:
    """Records every GET and replays queued responses in order.

    A response may be a FakeResponse or an exception instance; an exception is
    raised instead of returned, which is how transport failures are simulated.
    """

    def __init__(self, responses=None):
        self.headers: dict[str, str] = {}
        self.responses = list(responses or [])
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if not self.responses:
            raise AssertionError(f"FakeSession got an unexpected GET: {url} {params}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def last_params(self) -> dict:
        return self.calls[-1][1] or {}

    @property
    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


@pytest.fixture
def fake_clock():
    """A monotonic clock the test drives by hand, plus a sleep that advances it.

    Lets a test prove that pacing and backoff waited the right amount without
    the suite actually waiting.
    """

    state = {"now": 0.0, "slept": []}

    def clock() -> float:
        return state["now"]

    def sleep(seconds: float) -> None:
        state["slept"].append(seconds)
        state["now"] += seconds

    return state, clock, sleep
