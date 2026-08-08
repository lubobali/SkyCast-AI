"""National Weather Service adapter: active severe-weather alerts.

The second source, layered in exactly as the homework suggests. Open-Meteo
models the atmosphere; it does not know that a human forecaster in Shreveport
has issued a Flood Advisory for two named counties. Only NWS carries that, and
it is the part a traveller actually needs.

Descended from `weather_client.py` in SkyIndex-AI, which has been running
against this API in production. Three behaviours are inherited because each one
was learned the hard way:

  the User-Agent      api.weather.gov answers 403 without a descriptive one
                      naming the application and a contact address. This is
                      documented policy, not a rate-limit heuristic.

  no `limit` param    /alerts/active rejects it with a 400 - it exists only on
                      the archival /alerts endpoint. The failure is quiet: the
                      400 body carries no "features" key, so code that reaches
                      for it with a default reports "no active alerts" for a
                      state that has thirty. Capping happens after parsing.

  pacing              NWS publishes no numeric limit and throttles clients it
                      considers abusive.

Queried by `point=lat,lon` rather than by state. A statewide query for Texas
returns advisories for the Louisiana border while the user is in El Paso, which
is technically an answer and practically noise.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from http_client import HttpError, JsonHttpClient

logger = logging.getLogger(__name__)

NWS_API_BASE_URL = os.environ.get("NWS_API_BASE_URL", "https://api.weather.gov")
DEFAULT_RATE = float(os.environ.get("NWS_MAX_REQUESTS_PER_SECOND", "4"))

# Ordered worst-first, which is the order a person needs them in. Anything the
# API sends that is not on this list sorts last rather than crashing.
SEVERITY_ORDER = ("Extreme", "Severe", "Moderate", "Minor", "Unknown")


class AlertServiceError(RuntimeError):
    """api.weather.gov could not be reached or refused the request."""


def severity_rank(severity: Any) -> int:
    """Sort key: lower is more dangerous."""
    try:
        return SEVERITY_ORDER.index(str(severity))
    except ValueError:
        return len(SEVERITY_ORDER)


class NWSAlertClient:
    """Active severe-weather alerts for a point."""

    def __init__(
        self,
        *,
        user_agent: str,
        base_url: str | None = None,
        http: JsonHttpClient | None = None,
        max_requests_per_second: float | None = None,
        **http_kwargs: Any,
    ) -> None:
        if not user_agent or not user_agent.strip():
            raise ValueError(
                "api.weather.gov requires a descriptive User-Agent naming the "
                "application and a contact address, for example "
                "'(SkyCast-AI, you@example.com)'. It answers 403 without one."
            )
        self.user_agent = user_agent.strip()
        self.base_url = (base_url or NWS_API_BASE_URL).rstrip("/")
        self.http = http or JsonHttpClient(
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/geo+json",
            },
            requests_per_second=(
                DEFAULT_RATE if max_requests_per_second is None else max_requests_per_second
            ),
            **http_kwargs,
        )

    def active_alerts(
        self, latitude: float, longitude: float, limit: int = 10
    ) -> list[dict]:
        """Active NWS alerts covering one point.

        Args:
            latitude: Decimal degrees.
            longitude: Decimal degrees.
            limit: Most alerts to return, worst first. Capped after parsing,
                because /alerts/active rejects a `limit` query parameter.

        Returns:
            A list of alerts, most severe first. Empty means genuinely no
            active alerts, which is the normal case and is not an error.

        Raises:
            AlertServiceError: the service failed or refused the request.
        """
        try:
            payload = self.http.get_json(
                f"{self.base_url}/alerts/active",
                params={"point": f"{latitude},{longitude}"},
            )
        except HttpError as exc:
            raise AlertServiceError(f"Could not reach the alert service: {exc}") from exc

        alerts = [
            normalized
            for feature in (payload.get("features") or [])
            if (normalized := _normalize(feature)) is not None
        ]
        alerts.sort(key=lambda alert: severity_rank(alert["severity"]))
        return alerts[: max(1, limit)]


def _normalize(feature: dict) -> dict | None:
    """Flatten one GeoJSON alert feature into a plain dict.

    `description` and `instruction` are kept as separate fields rather than
    joined: they answer different questions - what is happening, and what to do
    about it - and an agent deciding whether to warn someone wants the second
    one on its own.
    """
    properties = (feature or {}).get("properties") or {}

    identifier = properties.get("id") or feature.get("id")
    event = properties.get("event")
    if not identifier or not event:
        # A feature with no event is not an alert anyone can act on.
        return None

    return {
        "id": str(identifier),
        "event": event,
        "severity": properties.get("severity") or "Unknown",
        "urgency": properties.get("urgency"),
        "certainty": properties.get("certainty"),
        "headline": properties.get("headline") or event,
        # areaDesc names the actual counties under the alert, which is more
        # precise than the point that was queried with.
        "area": properties.get("areaDesc"),
        "description": (properties.get("description") or "").strip() or None,
        "instruction": (properties.get("instruction") or "").strip() or None,
        "effective": properties.get("effective") or properties.get("onset"),
        "expires": properties.get("expires") or properties.get("ends"),
        "issued_by": properties.get("senderName"),
    }


__all__ = ["SEVERITY_ORDER", "AlertServiceError", "NWSAlertClient", "severity_rank"]
