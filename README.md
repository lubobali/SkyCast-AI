# SkyCast-AI

**A weather MCP server, and a Databricks Agent Bricks agent that uses it.**

| | |
|---|---|
| **GitHub** | https://github.com/lubobali/SkyCast-AI |
| **MCP server, Databricks App** | https://skycast-ai-1352785079224954.aws.databricksapps.com |
| **MCP server, public instance** | https://skycast.lubot.ai — `/mcp` needs a bearer token |
| **Registered MCP** | `bootcamp_students.lubo_skycast.skycast_mcp` |
| **Agent** | `skycast-agent` (Supervisor Agent) |

> The Databricks App URL requires workspace authentication and will not open for
> someone who is not signed in there. `https://skycast.lubot.ai/` is public and
> shows the same server's landing page and tool list.
>
> **Why there are two.** The App is the deployment the homework requires. The
> AI Gateway turned out to be unable to authenticate *to* a Databricks App, so a
> second instance of the same code runs somewhere the gateway can reach. The
> full diagnosis is in [Two deployments](#two-deployments-and-why).

Databricks AI Bootcamp, Day 3 homework. Built from the pattern in
[`EcZachly/databricks-lakebase-app-day-3`](https://github.com/EcZachly/databricks-lakebase-app-day-3)
(Agent Bricks + an Alpaca paper-trading MCP server), with weather in place of
trading.

---

## What it does

Ask it a question in English. It picks the right tool, calls it, and answers
from what came back:

> **"Should I bring a jacket to Austin this weekend?"**
> No jacket needed. Austin is forecast to reach 97°F Saturday and 102°F Sunday,
> both far above the 60°F threshold. Take sunscreen instead, the UV index peaks
> at 8.4. And Sunday is at or above the 95°F heat threshold, so outdoor plans
> there are workable but need care.

Every number in that answer came from a tool call. The 60°F and 95°F are the
server's own published thresholds, not the model's opinion about what counts as
cold.

---

## Architecture

```
                    ┌──────────────────────────────────┐
                    │   Databricks Agent Bricks        │
                    │   agent: skycast-agent           │
                    │   system prompt: agent/          │
                    │     system_prompt.md             │
                    └────────────────┬─────────────────┘
                                     │  registered as an external MCP
                                     │  (AI Gateway → MCPs)
                    ┌────────────────▼─────────────────┐
                    │   Databricks App                 │
                    │   weather_mcp_server.py          │
                    │   FastMCP, streamable HTTP       │
                    │                                  │
                    │   @mcp.tool x 5  ── thin ────────┤
                    │     clean args, call one         │
                    │     adapter, shape the result.   │
                    │     No HTTP. No judgement.       │
                    └───┬──────────────┬───────────┬───┘
                        │              │           │
          ┌─────────────▼───┐  ┌───────▼───────┐  ┌▼──────────────────┐
          │ weather_        │  │ nws_client.py │  │ recommendation.py │
          │ provider.py     │  │               │  │                   │
          │                 │  │ ADAPTER       │  │ PURE LOGIC        │
          │ ADAPTER         │  │ alerts        │  │ thresholds,       │
          │ current +       │  │               │  │ no network        │
          │ forecast        │  │               │  │                   │
          └────────┬────────┘  └───────┬───────┘  └───────────────────┘
                   │                   │
         ┌─────────▼─────────┐  ┌──────▼──────────────┐
         │  http_client.py   │  │  http_client.py     │
         │  pacing, retries, │  │  + User-Agent from  │
         │  readable errors  │  │  secret_store.py    │
         └─────────┬─────────┘  └──────┬──────────────┘
                   │                   │
         ┌─────────▼─────────┐  ┌──────▼──────────────┐
         │   Open-Meteo      │  │  api.weather.gov    │
         │   no key          │  │  no key, contact    │
         │                   │  │  string required    │
         └───────────────────┘  └─────────────────────┘
```

The split the homework asks for, in one line: **tools are thin, adapters hold
every HTTP call, and judgement lives on its own with no network under it.**
There is no `import requests` in `weather_mcp_server.py`, and there is no
threshold in it either.

---

## The tools

| Tool | Required? | What it does |
|---|---|---|
| `get_current_weather(location)` | required #1 | Temperature, apparent temperature, humidity, conditions, wind speed and direction, precipitation, day or night |
| `get_forecast(location, days)` | required #2 | Up to 16 days: high, low, chance of precipitation, expected precipitation, peak wind, peak UV, sunrise, sunset |
| `get_outdoor_recommendation(location, date)` | required #3 | Umbrella, jacket, sunscreen, and outdoor-plan advice — each with the rule that produced it |
| `get_severe_weather_alerts(location)` | **stretch** | Active NWS alerts for that point, worst first |
| `compare_cities(locations, date)` | **stretch** | Two to five places in one call, one bad city does not lose the rest |

`location` is `"City, ST"`, `"City, State Name"`, or `"lat,lon"`. A bare city
name is rejected: more than thirty states have a Springfield, and quietly
picking one returns confident weather for the wrong part of the country.

### The recommendation tool is not a passthrough

This is the one the rubric singles out, so here is exactly what it does. Every
threshold is a named constant in
[`recommendation.py`](mcp_server/recommendation.py), cited in the tool's
docstring, and published in the tool's own response under
`thresholds_applied` — so a user can check the arithmetic without reading the
source.

| Verdict | Rule |
|---|---|
| **umbrella** | precipitation probability ≥ **40%**, or ≥ **1.0 mm** expected |
| **jacket** | high < **60°F**, or high < **70°F** with wind above **15 mph** |
| **sunscreen** | peak UV index ≥ **6** |
| **outdoor** | `avoid` for a thunderstorm or an Extreme/Severe NWS alert; `take care` at ≥ **95°F**, ≥ **25 mph** wind, ≤ **20°F**, or fog/freezing rain/heavy precipitation; otherwise `good` |

Three things make it more than a threshold table:

**It returns the rule that fired, not a boolean.** `{"needed": true}` tells a
user nothing they can argue with. `"73% chance of precipitation, at or above the
40% threshold"` tells them what was measured, what the rule was, and lets them
overrule it. An agent relaying the first has to invent a justification; relaying
the second, it only reads one out.

**Wind is in the jacket rule.** 65°F is pleasant; 65°F in a 20 mph wind is not,
because wind strips away the layer of warm air the body holds against skin. A
bare temperature threshold cannot see the difference.

**An NWS alert outranks every number.** A human forecaster naming a hazard for a
named county beats a model's daily aggregate. That is the entire reason for
pulling in a second source.

---

## Weather APIs and auth

Two sources, which is what the homework suggests: *"start with Open-Meteo… if
you want alerts or US-specific severe weather data, layer in the NWS API as a
second tool."*

| Source | Used for | Auth | Why |
|---|---|---|---|
| **Open-Meteo** | current conditions, forecast, geocoding | none | No signup, no key, no card. Carries the two fields the recommendation logic needs that most free APIs omit: a per-day precipitation *probability*, and a UV index |
| **NWS (api.weather.gov)** | severe weather alerts | none, but a contact User-Agent is mandatory | The only source for a warning issued by a human forecaster for a named county. Open-Meteo models the atmosphere; it does not know a Flood Advisory has been issued |

### About the secret

The homework says: *"If your chosen API requires a key: store it as a Databricks
secret."* **Neither of these APIs requires a key.** Rather than invent one no
API would ever check, the pattern is demonstrated on the one value here that
genuinely benefits from it.

`api.weather.gov` refuses requests that do not identify the application and a
way to contact its operator — it answers **403** without a descriptive
`User-Agent`. That string is not a credential, but it does carry a personal
email address, and a personal email address in a public repository is exactly
what gets scraped. So it is treated as a secret: kept out of git, kept out of
`app.yaml`, and read at runtime through the same
`WorkspaceClient().secrets.get_secret()` path an API key would take.

See [`secret_store.py`](mcp_server/secret_store.py). Resolution order is
environment variable → Databricks secret scope → a generic non-personal
fallback, so the server runs locally, runs deployed, and never fails to start
because a scope has not been created yet.

```bash
databricks secrets create-scope lubo-skycast
databricks secrets put-secret lubo-skycast nws-user-agent \
  --string-value "(SkyCast-AI, your@email.com)"
```

Then grant the app's service principal `READ` on the scope —
[`setup_secrets.py`](setup_secrets.py) does both. Skipping the ACL is the
failure that looks like an outage: the app starts fine and then cannot read its
own secret, because the app's service principal is a different identity from the
human who created the scope.

---

## Running it

### Locally

```bash
git clone https://github.com/lubobali/SkyCast-AI.git
cd SkyCast-AI

python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env      # then put a real contact address in it
export NWS_USER_AGENT="(SkyCast-AI, your@email.com)"

python mcp_server/weather_mcp_server.py     # http://127.0.0.1:8000/mcp
```

Verify it, in another shell:

```bash
python scripts/smoke_test.py
```

That connects as a real MCP client, lists the tools, and calls every one of them
against the live APIs — including the failure cases, because a tool that raises
instead of returning an error is the bug that matters most here.

### On Databricks

1. **Push** to GitHub, then create a **Git folder** in the workspace pointing at
   the repo.
2. **Secrets**: run `python setup_secrets.py` (creates the scope, writes the
   contact string, grants the app's service principal READ).
3. **App**: Compute → Apps → Create app → Custom, source = the `mcp_server/`
   subfolder, deploy. `app.yaml` supplies the config; nothing sensitive is in it.
4. **Register the MCP**: AI Gateway → MCPs → **+ MCP** → paste the app URL with
   `/mcp` on the end. Databricks introspects the server and lists all five tools.
5. **Build the agent**: AI Gateway → Agents → create, attach the
   `skycast-weather` MCP, and paste the prompt from
   [`agent/system_prompt.md`](agent/system_prompt.md).

Verify the deployed server the same way as local:

```bash
python scripts/smoke_test.py --url https://<app>.databricksapps.com/mcp
```

---

## The agent

The system prompt is checked in at
[`agent/system_prompt.md`](agent/system_prompt.md), and the rest of the agent's
configuration - type, model, attached MCP, tool list, and the demo transcripts -
at [`agent/agent_config.md`](agent/agent_config.md). Both are in the repo so the
agent's behaviour is versioned next to the tools it calls, rather than living
only in a web form that nobody can diff.

It covers what the homework asks for — what the agent does, which tools to call
in what order, and guardrails — and the guardrails are specific rather than
aspirational:

- **Which tool, in what order.** Anything asking what to *wear*, *take*, or
  whether to *do* something outside routes to `get_outdoor_recommendation`, not
  to a raw forecast. Left to itself a model answers "should I bring a jacket"
  by reading a temperature and deciding for itself what counts as cold — fluent,
  unstated, and inconsistent between two identical questions a day apart.
- **Alerts are unconditional** for anything about travel, driving, or safety.
  A pleasant forecast and an active tornado warning are both true at once.
- **Never invent weather.** Each of the four `error_type` values is named, with
  the correct response to each. "Handle errors gracefully" is advice a model can
  satisfy while doing the wrong thing.
- **Null is not zero and null is not "no."**
- **An outage is not an all-clear.**

### Demonstration

Three natural-language questions, with the agent's tool calls and answers, are
transcribed in [`agent/agent_config.md`](agent/agent_config.md#demonstration)
and screenshotted in [`screenshots/`](screenshots/):

| # | Question | Tools the agent called |
|---|---|---|
| 1 | "Will it rain in Chicago tomorrow?" | `get_forecast` |
| 2 | "Should I bring a jacket to Austin this weekend?" | `get_forecast` → `get_outdoor_recommendation` |
| 3 | "I'm driving from Denver to Miami Friday, anything I should worry about?" | `compare_cities` → `get_severe_weather_alerts` |

---

## Two deployments, and why

The homework requires the MCP server to be *"deployed as its own Databricks
App"* (requirement 4) and the agent to be *"registered against your MCP server
as an external tool"* (requirement 5). Both are done. They could not be done
against the same instance, and the reason is worth writing down.

**The Databricks AI Gateway cannot authenticate to a Databricks App.** Every
authentication method the gateway's "Connect an existing MCP server" form
offers was tried, in order:

| Method | Result |
|---|---|
| **Dynamic Client Registration** | `DCR registration failed... Authorization server at .../oidc does not support DCR. The server metadata does not include a registration_endpoint.` Confirmed against the workspace's own `/oidc/.well-known/oauth-authorization-server`, which publishes `authorization_endpoint` and `token_endpoint` but no `registration_endpoint`. |
| **Bearer token (PAT)** | A personal access token is not an OAuth token. Databricks Apps accept only OAuth. A request carrying a PAT gets `302` to `/oidc/oauth2/v2.0/authorize` and never reaches the process. Verified with a direct `requests` call from a notebook. |
| **OAuth M2M** | Needs a service principal with an OAuth secret. Creating one returned `...ServicePrincipals is only accessible by admins.` This is a shared bootcamp workspace; the account is in `users`, not `admins`. |
| **OAuth U2M** | Needs the gateway's redirect URI registered against the app's OAuth client. That client is Databricks-managed and not editable. |

So the same code runs twice:

```
    same commit, same tools, same tests
                    |
      +-------------+-------------+
      |                           |
  Databricks App            skycast.lubot.ai
  requirement #4            requirement #5
  platform handles auth     bearer_auth.py handles auth
```

The public instance is a plain systemd service on a server that was already
running: its own directory, its own unprivileged user, its own venv, its own
port, bound to `127.0.0.1` with nginx terminating TLS in front. It shares
nothing with anything else on that host. `/mcp` requires a bearer token that
was generated on the server and has never been committed, printed, or
transmitted; the landing page and `/status` stay public because neither costs
an upstream API call nor reveals a secret.

The honest summary: this is not how it should work, and on a workspace where
the account had admin it would be one deployment and an OAuth M2M connection.
The homework's actual requirements are both met, and the workaround is one
module and one systemd unit.

---

## Deliberate deviations from the reference pattern

Stated here so they read as decisions rather than as mistakes.

**One shared HTTP client instead of a duplicated adapter.** The Day 3 repo keeps
a full copy of `alpaca_broker.py` in each app folder, and duplicates the retry
logic with it. Two adapters here talk to two different APIs but need the same
four behaviours — pacing, bounded retries, no retry on 4xx, and errors that name
the offending parameter — so those live once in
[`http_client.py`](mcp_server/http_client.py). The adapters stay separate; only
the plumbing is shared.

**No dashboard app.** The optional stretch dashboard is not built. The time went
into the second data source, the two stretch tools, and the test suite instead.

**Alerts are queried by point, not by state.** A statewide Texas query returns
advisories from the Louisiana border to a user in El Paso — technically an
answer, practically noise.

**`get_outdoor_recommendation` requests the full 16-day horizon** even when it
needs one day. The alternative is arithmetic on "how many days ahead is that
date", which needs the local date at the location — not known until after the
geocode, and wrong by a day if the server assumes UTC. One extra kilobyte is
cheaper than that bug.

**Docstrings are laid out around what FastMCP actually publishes.** FastMCP
lifts the `Args:` block into the JSON schema, one description per parameter,
and **discards the `Returns:` block entirely** — the agent only ever sees the
docstring text *above* the `Args:` line. So everything the agent must know is
stated above that line, and the Google-style `Args:`/`Returns:` blocks remain
below it for human readers. This was found by inspecting a running server, not
by reading the docs.

**`/status` exists because `/healthz` is not reachable.** `/healthz` is a de
facto reserved path: Databricks Apps intercepts it for its own liveness probing
and the request never reaches the process, so opening it returns an empty white
page - not an error, not the JSON, just nothing, which reads as a broken app.
`/status` serves the identical payload on a path no platform will claim. This is
the second time this has come up; on the previous project a UI polling
`/healthz` for its row counts showed "stats unavailable" on a perfectly healthy
app.

**`/status` reports where the NWS contact string came from.** Both the
Databricks secret and the fallback are valid contact strings that
api.weather.gov accepts, so a server quietly running on the fallback - because
the scope was never created, or the app's service principal was never granted
READ on it - behaves identically to one reading the secret. `"nws_contact":
"secret"` is the proof the whole path works. The source only, never the value.

**`secret_store.py`, not `secrets.py`.** Python ships a stdlib module named
`secrets`. This directory is on `sys.path` in the deployed app, so a local
`secrets.py` would shadow it for every library in the process — and the first
thing to break would be somebody else's token generation, a long way from here
and with nothing in the traceback pointing back.

---

## Tests

```bash
python -m pytest          # 228 tests
ruff check mcp_server tests --line-length 100
```

Every fixture under `tests/fixtures/` is a **real recorded response**, captured
from Open-Meteo and api.weather.gov with `curl` on 2026-08-08. A test that
asserts against invented JSON proves only that the author's imagination is
self-consistent.

Two things the suite is built around:

**No tool may ever raise.** A raise reaches the agent as a transport failure it
can only report as "the tool broke". A returned `{"error": ...}` is a sentence
it can act on — and one that tells it not to fill the gap itself. There is a
whole test class feeding every tool garbage to prove it.

**Mocks cannot prove the interesting things.** They cannot tell you the server
binds, the transport negotiates, the tools are discoverable over the wire, or
that api.weather.gov likes your User-Agent today. That is what
`scripts/smoke_test.py` is for, and running it is how the FastMCP docstring
behaviour above was found.

### A bug the tests did not catch, and live data did

The first version of `outdoor_activity` looked only at heat, cold, wind, and
alerts. Run against real data, Chicago for 2026-08-10 came back as:

```
2026-08-10  Thunderstorm with light hail  88.3/68.9F  →  "Good conditions for being outside."
```

88°F crosses no heat threshold. 17.8 mph crosses no wind threshold. There was no
NWS alert. Every unit test passed. Lightning simply does not appear in a daily
aggregate of temperature and wind, so the rule set had no way to see it.

Thunderstorm codes now override the numbers outright, the test that pins it
records where it came from, and the rule keys off the **WMO integer** rather
than the English translation — so editing a phrase in `WMO_CODES` can never
silently change a safety verdict.

---

## Requirements traceability

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 1 | MCP server built with FastMCP, tools via `@mcp.tool`, streamable HTTP | [`weather_mcp_server.py`](mcp_server/weather_mcp_server.py) | `screenshots/02-smoke-test.txt` |
| 2 | Separate adapter module, no raw `requests` in tool functions | [`weather_provider.py`](mcp_server/weather_provider.py), [`nws_client.py`](mcp_server/nws_client.py), [`http_client.py`](mcp_server/http_client.py) | no `requests` import in the server file |
| 3 | Secrets via `WorkspaceClient().secrets.get_secret()`, nothing committed | [`secret_store.py`](mcp_server/secret_store.py), [`setup_secrets.py`](setup_secrets.py) | `screenshots/05-secret-scope.png`, `screenshots/05b-status-secret.png` (`"nws_contact":"secret"`) |
| 4 | `requirements.txt` + `app.yaml`, deployed as its own Databricks App | [`mcp_server/app.yaml`](mcp_server/app.yaml), [`mcp_server/requirements.txt`](mcp_server/requirements.txt) | `screenshots/04-app-running.png`, `screenshots/03-landing-page.png` |
| 5 | Agent Bricks agent registered against the MCP as an external tool | `bootcamp_students.lubo_skycast.skycast_mcp`, agent `skycast-agent` | `screenshots/06-mcp-registered.png`, `screenshots/07-agent-config.png`. See [Two deployments](#two-deployments-and-why) |
| 6 | Clear system prompt: purpose, tool order, guardrails | [`agent/system_prompt.md`](agent/system_prompt.md), [`agent/agent_config.md`](agent/agent_config.md) | `screenshots/07-agent-config.png` |
| 7 | README: architecture, tool list, setup, API + auth used | this file | — |
| 8 | ≥3 natural-language questions with tool calls and answers | — | `screenshots/08-q1-chicago-and-q2-austin.png`, `screenshots/10-q3-denver-miami-drive.png`, transcribed in [`agent/agent_config.md`](agent/agent_config.md#demonstration) |
| — | **Required tool 1**: current conditions | `get_current_weather` | `screenshots/02-smoke-test.txt` |
| — | **Required tool 2**: forecast | `get_forecast` | `screenshots/02-smoke-test.txt` |
| — | **Required tool 3**: prediction / recommendation | `get_outdoor_recommendation` | `screenshots/02-smoke-test.txt` |
| — | **Stretch**: severe weather alerts | `get_severe_weather_alerts` | `screenshots/02-smoke-test.txt` |
| — | **Stretch**: compare multiple cities | `compare_cities` | `screenshots/02-smoke-test.txt` |
| — | Docstrings with Args / Returns | every tool | `test_every_tool_documents_args_and_returns_in_source` |
| — | Full test suite green, lint clean | `tests/` | `screenshots/12-tests.txt` |
| — | Bad location → clean error, not a stack trace | [`_failed()`](mcp_server/weather_mcp_server.py) | `screenshots/02-smoke-test.txt`, `TestNothingEverRaises` |
| — | Prediction applies a threshold and explains it in the docstring | [`recommendation.py`](mcp_server/recommendation.py) | `test_the_prediction_tool_states_its_thresholds` |
| — | No secrets committed, no hardcoded keys | `.gitignore`, `.env.example` | neither API uses a key; see [About the secret](#about-the-secret) |
| — | System prompt specific enough to prevent hallucinated weather | [`agent/system_prompt.md`](agent/system_prompt.md) | `screenshots/07-agent-config.png` |

---

## Layout

```
SkyCast-AI/
├── mcp_server/                  the Databricks App
│   ├── weather_mcp_server.py    5 @mcp.tool functions, thin
│   ├── weather_provider.py      ADAPTER: Open-Meteo
│   ├── nws_client.py            ADAPTER: api.weather.gov alerts
│   ├── http_client.py           shared: pacing, retries, readable errors
│   ├── recommendation.py        PURE LOGIC: thresholds, no network
│   ├── location.py              parsing "Chicago, IL" and "lat,lon"
│   ├── validation.py            cleaning tool arguments
│   ├── secret_store.py          Databricks secret resolution
│   ├── bearer_auth.py           auth for the public instance
│   ├── app.yaml                 deploy config, nothing sensitive
│   └── requirements.txt
├── agent/
│   ├── system_prompt.md         the agent's instructions, versioned
│   └── agent_config.md          type, model, MCP, tool list, demo transcripts
├── tests/                       228 tests
│   └── fixtures/                real recorded API responses
├── scripts/
│   └── smoke_test.py            calls a running server as an MCP client
├── screenshots/
├── setup_secrets.py
├── PLAN.md
└── README.md
```

---

## Related

Day 2 of the same bootcamp, and the source of `nws_client.py`, the deploy
pattern, and the secret handling:
[**SkyIndex-AI**](https://github.com/lubobali/SkyIndex-AI) — semantic search
over live National Weather Service text, using Lakebase and pgvector.

MIT licensed.
