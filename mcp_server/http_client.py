"""One JSON-over-HTTP client, shared by both weather adapters.

The reference project this is modelled on keeps a full copy of its adapter in
each app folder, and duplicates the retry logic with it. Two adapters here talk
to two different APIs but need the same four behaviours, so those live once:

  pacing            api.weather.gov publishes no numeric rate limit; it asks
                    for reasonable use and throttles clients that abuse it.
                    Spacing requests deliberately beats discovering the limit
                    by being throttled. Open-Meteo needs none, so its pacer is
                    switched off rather than special-cased.

  bounded retries   with exponential backoff, on 5xx and transport errors.

  no retry on 4xx   a 4xx is a statement about the request - coordinates
                    outside coverage, a malformed parameter - and repeating it
                    cannot change the answer. Retrying wastes the caller's time
                    and, on a rate-limit 429, makes the situation worse.

  readable errors   both APIs answer a bad request with a body naming the
                    offending parameter. Letting only "400 Client Error"
                    escape throws away the one piece of information that
                    identifies the mistake.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import requests

logger = logging.getLogger(__name__)


class HttpError(RuntimeError):
    """An upstream API could not be reached, or refused the request.

    Carries a message written for a user to read. The MCP tools surface it as
    an `error` field, so it must never be a traceback or a bare status code.
    """


class Pacer:
    """Spaces outbound requests to a fixed maximum rate.

    The clock and sleep function are injected so tests can prove the spacing
    without spending real seconds doing it.
    """

    def __init__(
        self,
        requests_per_second: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        now = self._clock()
        if self._last is not None:
            remaining = self.min_interval - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last = now


class JsonHttpClient:
    """GET JSON, with pacing, bounded retries, and errors worth reading."""

    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
        max_retries: int = 3,
        backoff: float = 0.5,
        requests_per_second: float = 0.0,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.backoff = backoff
        self._sleep = sleep
        self.session = session if session is not None else requests.Session()
        if headers:
            self.session.headers.update(headers)
        self._pacer = Pacer(requests_per_second, sleep, clock)

    def get_json(self, url: str, params: dict | None = None) -> Any:
        """GET `url` and return the decoded JSON body.

        Args:
            url: Absolute URL.
            params: Query string parameters.

        Returns:
            The decoded response body.

        Raises:
            HttpError: The request was refused, every retry failed, or the
                response was not JSON. The message is safe to show a user.
        """
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            self._pacer.wait()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except requests.HTTPError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status < 500:
                    raise HttpError(_explain(exc, status)) from exc
                last_error = exc
            except ValueError as exc:
                # A 200 whose body is not JSON. Retrying an upstream that is
                # serving an HTML error page behind a 200 does sometimes help,
                # so this falls through to the retry rather than raising.
                last_error = exc
            except requests.RequestException as exc:
                last_error = exc

            if attempt < self.max_retries:
                delay = self.backoff * (2 ** (attempt - 1))
                logger.warning(
                    "%s failed (attempt %s/%s), retrying in %.1fs: %s",
                    url, attempt, self.max_retries, delay, last_error,
                )
                self._sleep(delay)

        raise HttpError(
            f"Weather service did not respond after {self.max_retries} attempts "
            f"({last_error})."
        ) from last_error


def _explain(exc: requests.HTTPError, status: int) -> str:
    """Fold the API's own error body into a message worth showing.

    api.weather.gov answers with an RFC 7807 problem document naming the bad
    parameter; Open-Meteo answers with `{"reason": "..."}`. Both are far more
    useful than the status line, and neither is visible unless it is read out
    of the body deliberately.
    """
    details: list[str] = []
    try:
        body = exc.response.json()
    except Exception:  # noqa: BLE001 - a non-JSON error body is not itself an error
        body = None

    if isinstance(body, dict):
        for key in ("title", "detail", "reason", "error", "message"):
            value = body.get(key)
            if value and str(value) not in details:
                details.append(str(value))
        for parameter_error in body.get("parameterErrors") or []:
            details.append(
                f"{parameter_error.get('parameter')}: {parameter_error.get('message')}"
            )

    if details:
        return f"Weather service rejected the request (HTTP {status}): {'; '.join(details)}"
    return f"Weather service rejected the request (HTTP {status})."


__all__ = ["HttpError", "JsonHttpClient", "Pacer"]
