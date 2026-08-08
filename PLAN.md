# SkyCast-AI — Build Plan

**Homework:** Build Your Own Weather-Prediction MCP Server + Agent
**Bootcamp:** Databricks AI Bootcamp, Day 3 (Agent Bricks + MCP)
**Reference pattern:** `EcZachly/databricks-lakebase-app-day-3` (Alpaca paper-trading MCP)
**Target:** 100/100, submitted 2026-08-08
**Repo:** https://github.com/lubobali/SkyCast-AI

---

## What is being built

An MCP server exposing weather tools, plus a Databricks Agent Bricks agent that
uses them to answer natural-language weather questions. Both deploy as
Databricks Apps, mirroring Day 3's `mcp_server/` + `dashboard/` split.

### Reuse

This is not a green field. Two shipped projects supply most of the parts.

| From | File | Used for |
|---|---|---|
| SkyIndex-AI | `weather_client.py` | NWS alerts, geocoding, request pacing, RFC 7807 error detail |
| SkyIndex-AI | `validation.py` | cleaning tool arguments |
| SkyIndex-AI | `app.yaml`, `requirements.txt` | Databricks App deploy pattern, already proven |
| SkyIndex-AI | `setup_secrets.py` | secret scope + service principal READ ACL |
| Day 3 repo | `alpaca_mcp_server.py` / `alpaca_broker.py` | the thin-tool / fat-adapter split |

---

## Weather APIs

Two sources, deliberately. The homework recommends starting with Open-Meteo and
says to "layer in the NWS API as a second tool" for alerts. That is exactly the
split below, and the NWS half is code that has already been shipped and tested.

| Source | Used for | Auth |
|---|---|---|
| Open-Meteo | current conditions, N-day forecast, geocoding | none |
| NWS (api.weather.gov) | severe weather alerts | none, but requires a contact User-Agent |

---

## Phase 0 — De-risk (20 min, before any code)

The one thing that can lose the day: **Databricks Free Edition may not expose
Agent Bricks or external MCP registration.** Requirement #5 and three of the
required screenshots depend on it. Find out first, not at midnight.

- [ ] 0.1 Check the Databricks sidebar for **Agent Bricks** and **AI Gateway → MCP**
- [ ] 0.2 If present, proceed as planned
- [ ] 0.3 If absent, fall back to a small MCP client script that calls the deployed
      server and drives it with an LLM, screenshotted. README states the Free
      Edition limitation plainly, with evidence.
- [ ] 0.4 Confirm Apps can still be created (0 of 7 used as of Day 2)

---

## Phase 1 — Scaffold (30 min)

- [ ] 1.1 `git init`, LICENSE, `.gitignore`, `.env.example`
- [ ] 1.2 Directory structure, mirroring the Day 3 repo:

```
SkyCast-AI/
├── mcp_server/
│   ├── weather_mcp_server.py    thin @mcp.tool wrappers only
│   ├── weather_provider.py      adapter: all Open-Meteo HTTP + parsing
│   ├── nws_client.py            from SkyIndex-AI, alerts only
│   ├── recommendation.py        pure threshold logic, no network
│   ├── validation.py            from SkyIndex-AI
│   ├── app.yaml
│   └── requirements.txt
├── agent/
│   └── system_prompt.md
├── tests/
│   └── fixtures/
├── screenshots/
├── PLAN.md
└── README.md
```

- [ ] 1.3 Copy `weather_client.py` → `nws_client.py`; drop the document and
      embedding half, keep `parse_location`, `resolve`, `fetch_alerts`, the
      rate pacer, and `_with_problem_detail`
- [ ] 1.4 Copy `validation.py`, `app.yaml`, `requirements.txt` as starting points

---

## Phase 2 — Adapter module, tests first (90 min)

`weather_provider.py` is this project's `alpaca_broker.py`. The rubric requires
**no raw `requests` calls inside any `@mcp.tool` function** — all HTTP and
parsing lives here.

- [ ] 2.1 Tests and recorded-JSON fixtures for every method, before the method
- [ ] 2.2 `geocode(location)` — Open-Meteo geocoder, already written in SkyIndex-AI
- [ ] 2.3 `current_conditions(location)` → temperature, conditions, humidity, wind
- [ ] 2.4 `daily_forecast(location, days)` → per-day high/low, precipitation
      probability, precipitation mm, wind, conditions
- [ ] 2.5 WMO weather-code → human text mapping (Open-Meteo returns integers;
      the agent needs words)
- [ ] 2.6 Clean error types: `LocationNotFound`, `WeatherServiceError`. Every
      message is written to be read by a user, never a traceback

---

## Phase 3 — Recommendation logic (45 min)

The rubric singles this out: the prediction tool must *"do more than echo the
raw API"* and must *"apply some threshold/logic of your choosing and explain it
in the tool's docstring."*

- [ ] 3.1 `recommendation.py` — pure functions, no network, fully unit tested
- [ ] 3.2 Thresholds as named module constants, so the docstring can cite them:

| Recommendation | Rule |
|---|---|
| umbrella | precipitation probability ≥ 40%, or ≥ 1.0 mm expected |
| jacket | high < 60°F, or wind > 15 mph with high < 70°F |
| sunscreen | UV index ≥ 6 |
| outdoor activity unsafe | high ≥ 95°F, or wind ≥ 25 mph, or an active NWS alert |

- [ ] 3.3 Return **the rule that fired**, not a bare boolean:
      `{"umbrella": true, "because": "62% chance of rain Saturday, above the 40% threshold"}`
- [ ] 3.4 Docstring states every threshold and the reasoning behind it

---

## Phase 4 — MCP server (60 min)

- [ ] 4.1 FastMCP, streamable-HTTP, port 8000, following the Day 3 pattern
- [ ] 4.2 The three required tools, thin, with `Args:` / `Returns:` docstrings:
  - `get_current_weather(location)`
  - `get_forecast(location, days)`
  - `get_outdoor_recommendation(location, date)`
- [ ] 4.3 Two stretch tools, for extra credit:
  - `get_severe_weather_alerts(location)` — NWS, reusing shipped code
  - `compare_cities(locations, date)` — multi-city comparison
- [ ] 4.4 Every tool catches adapter errors and returns `{"error": "..."}`;
      nothing raises into the transport
- [ ] 4.5 The NWS contact string goes in a **Databricks secret**, not the repo.
      Neither API needs a key, so this is how the `_secret()` requirement is met
      honestly. The README says exactly that.
- [ ] 4.6 Run locally, verify with MCP Inspector or curl. **Screenshot the tool list.**

---

## Phase 5 — Deploy (45 min)

- [ ] 5.1 Push to GitHub, create the Databricks Git folder
- [ ] 5.2 Create App → Custom → source `mcp_server/` → deploy
- [ ] 5.3 Secret scope plus **service principal READ ACL** — the step that cost an
      hour on Day 2; `setup_secrets.py` already handles it
- [ ] 5.4 Verify the App URL responds
- [ ] 5.5 Screenshots: App running, deploy log, secret scope

---

## Phase 6 — Agent (45 min)

- [ ] 6.1 AI Gateway → MCPs → Add MCP → paste the App URL; confirm Databricks
      introspects all five tools. **Screenshot.**
- [ ] 6.2 Build the Agent Bricks agent against that MCP
- [ ] 6.3 `agent/system_prompt.md`, checked into the repo, covering:
  - `get_current_weather` for "right now", `get_forecast` for future days
  - always call `get_outdoor_recommendation` before advising on clothing or plans
  - check `get_severe_weather_alerts` when the user mentions travel or safety
  - **never state a temperature or condition that did not come from a tool call**
  - if a location will not resolve, ask the user to clarify as "City, ST"
  - if a tool returns an error, say so; never substitute background knowledge
- [ ] 6.4 Three demo questions, each screenshotted **with the tool calls visible**:
  1. "Will it rain in Chicago tomorrow?"
  2. "Should I bring a jacket to Austin this weekend?"
  3. "I'm driving from Denver to Miami Friday, anything I should worry about?"
     (forces alerts plus multi-city)

---

## Phase 7 — Docs and submit (45 min)

- [ ] 7.1 README: architecture diagram, tool table, setup steps, and the weather
      API + auth choice with its reasoning
- [ ] 7.2 **Traceability table** mapping every rubric line to a file and a
      screenshot. This is what earned full marks on Day 2.
- [ ] 7.3 A "deliberate deviations" section, naming every choice that departs
      from the reference pattern before a grader can read it as a mistake
- [ ] 7.4 Full test suite green, ruff clean
- [ ] 7.5 App URL and GitHub URL at the **top** of the README, inside the zip
- [ ] 7.6 Zip and submit

---

## Rules for the day

1. **Screenshot the moment a thing works.** Free Edition stops an app roughly 24
   hours after it is started or redeployed, and deletes idle resources rather
   than pausing them. Evidence captured late is evidence not captured.
2. **Nothing deploys before its tests are green.** No "pre-existing failures".
3. **No secrets in git.** Ever.

---

## Requirements checklist (from the homework)

| # | Requirement | Phase |
|---|---|---|
| 1 | MCP server built with FastMCP, tools via `@mcp.tool`, streamable-HTTP | 4 |
| 2 | Separate adapter module, no raw `requests` in tool functions | 2 |
| 3 | Any API key stored as a Databricks secret, never committed | 4.5 |
| 4 | `requirements.txt` + `app.yaml`, deployed as its own Databricks App | 5 |
| 5 | Agent Bricks agent registered against the MCP as an external tool | 6 |
| 6 | Clear system prompt: what to do, tool order, guardrails | 6.3 |
| 7 | README: architecture, tool list, setup, API + auth used | 7 |
| 8 | At least 3 natural-language questions with tool calls and answers | 6.4 |

### Required tools

| Capability | Tool | Phase |
|---|---|---|
| Current conditions | `get_current_weather` | 4.2 |
| Forecast | `get_forecast` | 4.2 |
| Prediction / recommendation | `get_outdoor_recommendation` | 4.2 |

### Stretch (extra credit)

| Capability | Tool | Phase |
|---|---|---|
| Severe weather alerts | `get_severe_weather_alerts` | 4.3 |
| Compare multiple cities | `compare_cities` | 4.3 |
| Dashboard app | optional, only if time allows | — |
