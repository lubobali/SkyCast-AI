# SkyCast-AI — agent configuration

The deployed agent, written down. The homework asks for the agent config
alongside the server, and a screenshot of a web form is not something anyone can
diff, so this is the same information as text.

The instructions themselves are in [`system_prompt.md`](system_prompt.md).

---

## The agent

| | |
|---|---|
| **Name** | `skycast-agent` |
| **Type** | Supervisor Agent (Databricks Agent Bricks) |
| **Model** | `claude-haiku-4-5` |
| **Attached MCP** | `bootcamp_students.lubo_skycast.skycast_mcp` |
| **Tools exposed** | all 5, with "automatically include tools added in future" on |
| **Instructions** | [`system_prompt.md`](system_prompt.md), pasted verbatim |
| **Also attached** | `python_exec` — attached by Agent Bricks, cannot be removed |

**On the model.** `gpt-5-6-sol` was tried first and refused outright:
*"Function tools with reasoning_effort are not supported for gpt-5.6-sol in
/v1/chat/completions."* `claude-sonnet-5` returned
*"The model endpoint is unavailable or rate-limited"* after five attempts.
`claude-haiku-4-5` worked immediately, and is also the model with the most
throughput in this workspace.

**On `python_exec`.** Agent Bricks attaches a Python interpreter to every
Supervisor agent and offers no way to detach it. An interpreter is exactly what
a model reaches for when it would rather work something out than call a tool, so
the system prompt names it and says why its output would be fiction.

---

## The MCP service

| | |
|---|---|
| **Unity Catalog name** | `bootcamp_students.lubo_skycast.skycast_mcp` |
| **Connection** | `bootcamp_students.lubo_skycast.skycast_mcp_connection` |
| **Server URL** | `https://skycast.lubot.ai/mcp` |
| **Auth** | Bearer token, generated on the server, never committed |
| **Status** | Active |

Why this URL and not the Databricks App URL: see
[Two deployments](../README.md#two-deployments-and-why). Short version — the AI
Gateway cannot authenticate to a Databricks App, and every method its own form
offers was tried and ruled out.

## Tool list, as the agent sees it

| Tool | Required | Optional |
|---|---|---|
| `get_current_weather` | `location` (string) | — |
| `get_forecast` | `location` (string) | `days` (integer) |
| `get_outdoor_recommendation` | `location` (string) | `date` (any) |
| `get_severe_weather_alerts` | `location` (string) | `limit` (integer) |
| `compare_cities` | `locations` (array) | `date` (any) |

---

## Demonstration

Three natural-language questions, the tools the agent chose, and what it
answered. Screenshots of the same runs are in
[`../screenshots/`](../screenshots/).

### 1. "Will it rain in Chicago tomorrow?"

**Tools called:** `get_forecast(location="Chicago, IL", days=2)`

> Yes, rain is expected in Chicago tomorrow (August 9th). The forecast shows
> moderate drizzle with a 43% chance of precipitation and about 1.1 mm expected.
> The high will be 81°F and the low 66°F, with winds up to 14.5 mph.

Every number traces to the tool's response. Nothing was inferred.

### 2. "Should I bring a jacket to Austin this weekend?"

**Tools called:** `get_forecast`, then **two parallel**
`get_outdoor_recommendation` calls, one for Saturday and one for Sunday.

> No, you won't need a jacket in Austin this weekend. Both Saturday (high of
> 97°F) and Sunday (high of 103°F) will be well above the **60°F threshold**.
>
> However, you should bring sunscreen — the UV index reaches 8.2, which is high.
> Also be prepared for the heat, as both days will be at or above **95°F**, so
> outdoor plans are workable but take care to stay hydrated.

This is the routing rule in the system prompt doing its job. Asked the same
question with no system prompt, the model called `get_forecast` alone and
answered *"you may want to bring an umbrella"* — a fluent guess with no stated
rule behind it. Here it cites the server's own published thresholds, so the user
can disagree with the advice on the evidence.

### 3. "I'm driving from Denver to Miami on Friday. Anything I should worry about?"

**Tools called:** `get_forecast` and `get_severe_weather_alerts` for **both**
cities — four calls. Nothing in the question mentioned alerts.

> **Denver departure (Friday, August 12):**
> - **Heat Advisory** (Moderate severity) — temperatures of 98–103°F with warm
>   overnight lows through Sunday evening...
> - **Air Quality Alerts** — ozone and fine particulate matter in the Moderate to
>   Unhealthy for Sensitive Groups range...
> - Light drizzle expected with 53% chance of precipitation and winds up to 23 mph.
>
> **Miami arrival (Friday):** No active weather alerts. Thunderstorms forecast
> with 11% chance of precipitation. High of 89°F.

The unconditional-alerts rule for travel questions, and the clearest argument
for the second data source: the Heat Advisory and the Air Quality Alert are
issued by human forecasters at NWS. Open-Meteo models the atmosphere and has no
way to know either exists.

---

## Reproducing this

1. Deploy the MCP server (see the [README](../README.md#on-databricks)).
2. AI Gateway → MCPs → **+ MCP** → *Connect an existing MCP server*. Give it a
   catalog and schema you can `CREATE CONNECTION` in, the server URL with `/mcp`
   on the end, and its bearer token. **Create & load tools** should find all five.
3. Agents → **Create Agent** → **Supervisor Agent**.
4. **Add a UC MCP Service** → the service from step 2.
5. Expand **Instructions** and paste [`system_prompt.md`](system_prompt.md).
6. Set the model to `claude-haiku-4-5`.
