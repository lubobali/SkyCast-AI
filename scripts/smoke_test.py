"""Call a running SkyCast-AI MCP server the way an agent does.

Not a unit test. The suite mocks every network boundary, which is what makes it
fast and what makes it prove logic - but a mocked boundary cannot tell you that
the server binds, that the transport negotiates, that the tools are discoverable
over the wire, or that api.weather.gov likes your User-Agent today.

That gap is where the interesting failures live. On the last project every unit
test passed while the first live query died on a SQL syntax error, because a
fake cursor records SQL, it does not parse it.

Usage:
    python scripts/smoke_test.py                      # http://127.0.0.1:8000/mcp
    python scripts/smoke_test.py --url https://<app>.databricksapps.com/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from fastmcp import Client

CASES: list[tuple[str, dict]] = [
    ("get_current_weather", {"location": "Chicago, IL"}),
    ("get_forecast", {"location": "Austin, TX", "days": 3}),
    ("get_outdoor_recommendation", {"location": "Denver, CO"}),
    ("get_severe_weather_alerts", {"location": "Miami, FL"}),
    ("compare_cities", {"locations": ["Chicago, IL", "Miami, FL"]}),
    # The failure paths matter as much as the happy ones: an agent's honesty
    # depends on these coming back as readable errors rather than as crashes.
    ("get_current_weather", {"location": "Springfield"}),
    ("get_current_weather", {"location": "Toronto, Ontario"}),
    ("get_outdoor_recommendation", {"location": "Chicago, IL", "date": "tomorrow"}),
]


def unwrap(result) -> dict:
    """Pull the tool's dict out of whatever the client wrapped it in."""
    if getattr(result, "structured_content", None):
        payload = result.structured_content
        # FastMCP wraps a bare return value under "result".
        return payload.get("result", payload) if isinstance(payload, dict) else payload
    if getattr(result, "content", None):
        try:
            return json.loads(result.content[0].text)
        except Exception:  # noqa: BLE001
            return {"raw": result.content[0].text}
    return {}


async def run(url: str, token: str | None = None) -> int:
    failures = 0

    # The deployment behind a bearer token needs the header; the Databricks App
    # deployment needs no header at all, because the platform authenticates the
    # request before it arrives. One script, both targets.
    target = url
    if token:
        from fastmcp.client.transports import StreamableHttpTransport

        target = StreamableHttpTransport(
            url=url, headers={"Authorization": f"Bearer {token}"}
        )

    async with Client(target) as client:
        tools = await client.list_tools()
        print(f"Connected to {url}")
        print(f"{len(tools)} tools discovered:\n")
        for tool in sorted(tools, key=lambda item: item.name):
            summary = (tool.description or "").strip().splitlines()[0]
            print(f"  {tool.name:<28} {summary}")
        print()
        print("-" * 78)

        for name, arguments in CASES:
            label = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
            print(f"\n{name}({label})")
            try:
                payload = unwrap(await client.call_tool(name, arguments))
            except Exception as exc:  # noqa: BLE001
                # A raise here is the failure this script exists to catch: the
                # tools are supposed to return errors, never throw them.
                print(f"  RAISED: {type(exc).__name__}: {exc}")
                failures += 1
                continue

            if "error" in payload:
                print(f"  error [{payload.get('error_type')}]: {payload['error']}")
                continue

            for key in ("summary", "conditions", "temperature_f", "count", "compared"):
                if key in payload:
                    print(f"  {key}: {payload[key]}")
            if "days" in payload:
                for day in payload["days"]:
                    print(
                        f"  {day['date']}  {day['conditions']:<30} "
                        f"{day['high_f']}/{day['low_f']}F  "
                        f"rain {day['precipitation_probability_pct']}%"
                    )
            if "comparison" in payload:
                for entry in payload["comparison"]:
                    print(f"  {entry['location']:<20} {entry['outdoor_advice']:<10} {entry['summary']}")
            if payload.get("alerts"):
                for alert in payload["alerts"][:3]:
                    print(f"  [{alert['severity']}] {alert['event']} - {alert['area']}")

    print("\n" + "-" * 78)
    print("every tool returned a result" if not failures else f"{failures} tool(s) RAISED")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument(
        "--token",
        default=os.environ.get("SKYCAST_BEARER_TOKEN"),
        help="Bearer token, if the target requires one. Defaults to $SKYCAST_BEARER_TOKEN.",
    )
    arguments = parser.parse_args()
    return asyncio.run(run(arguments.url, arguments.token))


if __name__ == "__main__":
    sys.exit(main())
