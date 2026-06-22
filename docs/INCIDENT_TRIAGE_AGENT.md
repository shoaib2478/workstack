# Automated Incident Triage Agent — Run & Test Guide

End-to-end guide for the Phase 5 agent: Celery chord → LangGraph → MCP HR lookup.

**Code:** `backend/apps/incidents/tasks.py`

**Quick read:** [§0 — The Log Autopsy (22 seconds)](#0-quick-read--the-log-autopsy-22-seconds) — annotated Celery timestamps, sequence diagram, and phase-by-phase narrative.

[← Architecture](AGENT_ARCHITECTURE.md) · [LangGraph](LANGGRAPH_DEEP_DIVE.md) · [LangChain + MCP](LANGCHAIN_MCP_INTEGRATION.md)

---

## Table of Contents

0. [Quick read — The Log Autopsy (22 seconds)](#0-quick-read--the-log-autopsy-22-seconds)
1. [What this agent does](#1-what-this-agent-does)
2. [Prerequisites](#2-prerequisites)
3. [Architecture walkthrough — group + chord](#3-architecture-walkthrough)
4. [Line-by-line code map](#4-line-by-line-code-map)
5. [Version A vs Version B](#5-version-a-vs-version-b)
6. [How to test](#6-how-to-test)
7. [Expected output](#7-expected-output)
8. [Troubleshooting](#8-troubleshooting)
9. [Interview pitch](#9-interview-pitch)
10. [Conclusion — what this flow proves](#10-conclusion--what-this-flow-proves)

---

## 0. Quick read — The Log Autopsy (22 seconds)

**One real Celery run, annotated.** Timestamps below are from a successful production-style test (`Task succeeded in 22.17s`). Gemini wording varies run-to-run; the **orchestration sequence does not**.

> **Note:** The mock GitHub author in `tasks.py` is currently `katrina@newhire.com`. An earlier run used `shuaib@workstack.dev` — same flow, different email in logs. Align the mock author with a real `User.username` in Postgres for a full manager lookup.

### Complete flow (one glance)

```mermaid
sequenceDiagram
    participant Shell as Django shell
    participant Celery as Celery workers
    participant Chord as Chord callback
    participant LG as LangGraph ReAct
    participant MCP as hr_server stdio
    participant DB as PostgreSQL
    participant Gemini as Gemini 2.5 Flash

    Shell->>Celery: trigger_incident_workflow()
    par Scatter ~1s total
        Celery->>Celery: fetch_datadog_metrics
        Celery->>Celery: fetch_github_commits
        Celery->>Celery: fetch_slack_alerts
    end
    Celery->>Chord: aggregated_logs list
    Chord->>LG: run_mcp_enhanced_triage
    LG->>MCP: ListToolsRequest
    MCP-->>LG: get_employee_manager schema
    LG->>Gemini: prompt + logs + tool schemas
    Gemini-->>LG: CallToolRequest
    LG->>MCP: get_employee_manager email
    MCP->>DB: ORM lookup sync_to_async
    DB-->>MCP: user / manager / not found
    MCP-->>LG: tool result
    LG->>Gemini: full history + tool result
    Note over Gemini: 503 UNAVAILABLE × 2<br/>langchain-google-genai auto-retry
    Gemini-->>LG: final incident report
    LG->>LG: extract_message_text
    LG-->>Celery: plain string report
```

### Timeline breakdown

| Phase | Timestamp | Duration | What happened |
|-------|-----------|----------|---------------|
| **Scatter** | `04:58:46.487` | ~1.0s | `fetch_github_commits` finished |
| **Scatter** | `04:58:46.503` | ~1.0s | `fetch_slack_alerts` finished (parallel with Datadog + GitHub) |
| **Gather** | `04:58:46.503` | instant | `run_mcp_enhanced_triage` received — chord bundled 3 JSON blobs into one list |
| **ReAct Turn 1** | `04:58:46.503+` | ~3s | Agent spawned `hr_server.py --transport stdio`; `ListToolsRequest` → tool schemas |
| **Gemini call 1** | `04:58:49.416` | ~3s | Gemini read logs, chose `get_employee_manager` instead of plain text |
| **MCP tool** | `04:58:49.416+` | &lt;1s | `CallToolRequest` → live Postgres query via `sync_to_async` |
| **503 drama** | `04:58:53.717` | +1.8s backoff | Gemini 503 — library retried automatically |
| **503 drama** | `04:58:59.963` | +2.1s backoff | Second 503 — retried again |
| **ReAct Turn 2** | `04:59:08.667` | ~8s | Gemini 200 OK — final report streamed |
| **Done** | `04:59:08.675` | **22.17s total** | Task succeeded; plain-text report returned |

```mermaid
flowchart LR
    A["0–1s<br/>Scatter<br/>3 parallel fetchers"] --> B["~1s<br/>Gather<br/>chord callback"]
    B --> C["1–4s<br/>ReAct Turn 1<br/>ListTools + Gemini"]
    C --> D["4–5s<br/>MCP CallTool<br/>Postgres lookup"]
    D --> E["7–17s<br/>503 retries<br/>auto backoff"]
    E --> F["17–22s<br/>ReAct Turn 2<br/>final report"]
```

---

### The Scatter (parallel execution)

```
04:58:46,487: fetch_github_commits finished in 1.01 seconds.
04:58:46,503: fetch_slack_alerts finished in 1.02 seconds.
```

**What happened:** Celery grabbed the three deterministic fetchers and ran them **simultaneously** on workers. Because we used a `group`, work that would take ~3 seconds sequentially finished in **~1 second**.

---

### The Gather (chord callback)

```
04:58:46,503: run_mcp_enhanced_triage received.
```

**What happened:** The instant the last parallel task finished, Celery's **chord** bundled the GitHub, Slack, and Datadog JSON into a single list and dispatched `run_mcp_enhanced_triage(aggregated_logs, server_id)`.

---

### LangGraph ReAct — Turn 1

```
04:58:46.503+: Processing request of type ListToolsRequest
04:58:49,416: HTTP Request to Gemini ... "HTTP/1.1 200 OK"
```

**What happened:**

1. The agent booted inside the Celery callback (`asyncio.run`).
2. `MultiServerMCPClient` spawned `hr_server.py --transport stdio` and asked: *"What tools do you have?"*
3. The daemon replied with the JSON schema for `get_employee_manager`.
4. LangGraph sent **aggregated logs + prompt + tool schemas** to Gemini.
5. Gemini read the logs, identified the breaking commit author, and returned a **tool call** (not final prose).

---

### MCP tool execution (live DB query)

```
04:58:49.416+: Processing request of type CallToolRequest
```

**What happened:** LangGraph intercepted the tool call and asked the FastMCP daemon to run `get_employee_manager(email=...)`. The daemon queried PostgreSQL via Django ORM wrapped in `sync_to_async`, then returned the result into the message history.

In the annotated run, the email was `shuaib@workstack.dev` — **no matching `User.username`** in the local DB, so the tool returned an error string. The agent handled that gracefully in the final report.

---

### Fault tolerance — the 503 drama

```
04:58:53,717: HTTP Request to Gemini ... "HTTP/1.1 503 Service Unavailable"
04:58:59,963: HTTP Request to Gemini ... "HTTP/1.1 503 Service Unavailable"
Retrying google.genai._api_client... in 1.82 seconds
Retrying google.genai._api_client... in 2.08 seconds
```

**What happened:** After appending the tool result, LangGraph sent the full conversation back to Gemini for synthesis. Google's API returned **503 UNAVAILABLE** (high demand).

**Why this is good:** `langchain-google-genai` caught the HTTP error, applied **exponential backoff**, and retried — **without crashing the Celery worker** and without custom retry code in `tasks.py`.

---

### LangGraph ReAct — Turn 2 (final answer)

```
04:59:08,667: HTTP Request to Gemini ... "HTTP/1.1 200 OK"
```

**What happened:** On the third attempt, Gemini responded. It read the full history (prompt → tool call → tool result) and generated the incident report. `extract_message_text()` in `apps/incidents/parser.py` flattened Gemini's block-style content into a plain string for logging and the Celery return value.

---

### The result

```
04:59:08,671: --- FINAL AI AGENT OUTPUT ---
Manager Contact: Attempted to retrieve manager details for shuaib@workstack.dev
               but no user was found with that email.
04:59:08,675: Task apps.incidents.tasks.run_mcp_enhanced_triage[...] succeeded in 22.17s
```

**What happened:** The agent **completed successfully**:

- Identified the incident from parallel logs (CPU 99%, nginx commit, Slack 502s).
- Called MCP and queried the real database.
- Adapted when HR lookup failed — escalated to on-call instead of hallucinating a manager.

**Time budget (~22s):** ~1s parallel fetch + ~3s first Gemini turn + ~10s Google outages/retries + ~8s final generation.

---

## 1. What this agent does

**Scenario:** Production server `srv-production-01` is failing.

| Step | System | Action |
|------|--------|--------|
| 1 | Celery `group` | Fetch Datadog, GitHub, Slack data **in parallel** |
| 2 | Celery `chord` | Pass aggregated logs to triage task |
| 3 | LangGraph agent | Read logs; identify commit author |
| 4 | MCP tool | Look up author's manager in Workstack HR DB |
| 5 | LangGraph agent | Draft incident report to manager |

---

## 2. Prerequisites

### Services running

```bash
make up
# Requires: db, redis, rabbitmq, celery, mcp_hr_daemon (optional for stdio path)
```

### Environment

```env
GEMINI_API_KEY=your_key
DATABASE_URL=...
CELERY_BROKER_URL=...
```

### Install LangChain stack (pinned in requirements)

LangChain packages are in `backend/requirements/base.txt`:

```text
langchain-core~=1.4.8
langchain-google-genai~=4.2.5
langchain-mcp-adapters~=0.3.0
langgraph~=1.2.6
```

Rebuild after changes:

```bash
docker compose build web celery
docker compose up -d
```

See [LANGCHAIN_MCP_INTEGRATION.md](LANGCHAIN_MCP_INTEGRATION.md) §8 for why `~=` beats `>=`.

### HR data in database

The agent calls `get_employee_manager` for the commit author email. Ensure a user exists:

- Username/email must match `fetch_github_commits` mock author (currently `katrina@newhire.com`)
- Employee record with a manager in org chart (Treebeard parent)

---

## 3. Architecture walkthrough

### Celery Canvas: `group` + `chord` (both are used)

A common confusion: **`group` and `chord` work together** — the chord was not removed.

```python
# Section 3 in tasks.py — trigger_incident_workflow()

# 1. SCATTER — group bundles parallel tasks
parallel_fetchers = group(
    fetch_datadog_metrics.s(server_id),
    fetch_github_commits.s(server_id),
    fetch_slack_alerts.s(server_id),
)

# 2. GATHER — chord waits for ALL group tasks, then runs callback
workflow = chord(parallel_fetchers)(run_mcp_enhanced_triage.s(server_id))
```

| Piece | Role |
|-------|------|
| **`group(...)`** | Runs 3 fetchers **in parallel** across Celery workers |
| **`chord(group)(callback)`** | Waits until **all 3 finish**, collects return values into a **list**, passes list as **first arg** to `run_mcp_enhanced_triage` |
| **`.s(server_id)`** | Binds `server_id` as the **second** arg to the callback |

```mermaid
flowchart LR
    Trigger[trigger_incident_workflow] --> Group

    subgraph Group["group — parallel scatter"]
        T1[fetch_datadog]
        T2[fetch_github]
        T3[fetch_slack]
    end

    Group -->|"all 3 succeed"| Chord["chord gather"]
    Chord -->|"aggregated_logs list + server_id"| Triage[run_mcp_enhanced_triage]
    Triage --> Agent[LangGraph + MCP]
```

**Without `chord`:** you would only have a `group` — no automatic callback with merged results.  
**Without `group`:** you would run fetchers sequentially — slower, still no gather pattern.

This is the full **Scatter-Gather** Celery Canvas pattern.

### Full pipeline

```mermaid
flowchart TB
    Trigger[trigger_incident_workflow] --> Group

    subgraph Group["Celery group — parallel"]
        T1[fetch_datadog_metrics]
        T2[fetch_github_commits]
        T3[fetch_slack_alerts]
    end

    Group --> Chord[chord header completes]
    Chord --> Triage[run_mcp_enhanced_triage]

    subgraph Agent["LangGraph inside triage task"]
        A1[agent node — Gemini]
        A2[execute_tools — MCP stdio today]
        A1 --> A2
        A2 --> A1
    end

    Triage --> Agent
    A2 --> HR[hr_server.py]
    HR --> PG[(PostgreSQL)]
```

---

## 4. Line-by-line code map

### Section 1 — Muscle (lines 15–28)

```python
@shared_task
def fetch_datadog_metrics(server_id):
    time.sleep(1)
    return {"source": "Datadog", "cpu_usage": "99%", ...}
```

Three independent Celery tasks. **No AI.** Simulate external API latency with `sleep`.

### Section 2 — Brain entry (lines 35–38)

```python
@shared_task
def run_mcp_enhanced_triage(aggregated_logs, server_id):
    return asyncio.run(_async_agent_execution(aggregated_logs, server_id))
```

Celery chord passes `aggregated_logs` as **first argument** automatically — list of three dicts from the group.

### Section 2 — MCP + graph (lines 41–94)

| Lines | Purpose |
|-------|---------|
| 42 | LangChain Gemini chat model |
| 43 | Path to shared `hr_server.py` |
| 57–59 | `create_react_agent(llm, mcp_tools)` — ReAct + `add_messages` built-in |
| 61–67 | Prompt with pre-fetched Celery logs |
| 69 | `agent.ainvoke(...)` |
| 71–95 | Commented manual graph + `Annotated[list, add_messages]` — use when adding HITL/routing |

### Section 3 — Trigger (lines 93–104)

```python
parallel_fetchers = group(...)
workflow = chord(parallel_fetchers)(run_mcp_enhanced_triage.s(server_id))
```

`.s(server_id)` binds `server_id` as second arg to callback after chord results.

---

## 5. Version A vs Version B

| | Version A — logs only graph | Version B — MCP enhanced (current) |
|---|----------------------------|-------------------------------------|
| Chord fetchers | Same | Same |
| LangGraph | Fixed nodes: analyze → decide | ReAct loop: agent ↔ tools |
| MCP | Not used | `get_employee_manager` |
| Manager lookup | Hardcoded or guessed | Real Postgres via MCP |
| File | Conceptual / earlier draft | `incidents/tasks.py` |

Workstack implements **Version B** using `create_react_agent(llm, mcp_tools)`. The manual `StateGraph` + `add_messages` pattern is preserved in **comments** in `tasks.py` for when you add custom workflow nodes. See [LANGGRAPH_DEEP_DIVE.md](LANGGRAPH_DEEP_DIVE.md) §7–8.

---

## 6. How to test

### Step 1 — Confirm Celery worker sees tasks

```bash
docker compose logs celery -f
```

Look for registered task names including `apps.incidents.tasks`.

### Step 2 — Launch workflow from Django shell

```bash
docker compose exec web python manage.py shell
```

```python
from apps.incidents.tasks import trigger_incident_workflow
trigger_incident_workflow()
```

### Step 3 — Watch Celery logs

You should see:

1. Three fetch tasks start and complete (~1s each, parallel)
2. `run_mcp_enhanced_triage` starts
3. MCP subprocess spawns (stdio) or connects to daemon
4. LangGraph agent/tool cycles in logs
5. `--- FINAL AI AGENT OUTPUT ---` printed

### Step 4 — Run automated flow tests

```bash
# Unit tests (fetchers + parser) always run; full flow skips without GEMINI_API_KEY
docker compose exec web python manage.py test apps.incidents.tests.test_triage_flow -v 2

# MCP HR daemon alone (no Gemini)
docker compose exec web python manage.py test apps.organizations.tests.test_mcp_sse -v 2
```

### Step 5 — Optional: test chord without AI

Temporarily replace callback with a print task to verify Canvas wiring before debugging LangGraph.

---

## 7. Expected output

```
Orchestration Canvas launched! Task ID: <uuid>
```

In Celery worker logs (abbreviated):

```
[INFO] Task apps.incidents.tasks.fetch_datadog_metrics[...] succeeded
[INFO] Task apps.incidents.tasks.fetch_github_commits[...] succeeded
[INFO] Task apps.incidents.tasks.fetch_slack_alerts[...] succeeded
[INFO] Task apps.incidents.tasks.run_mcp_enhanced_triage[...] received

--- FINAL AI AGENT OUTPUT ---
Subject: Critical Incident — srv-production-01
...
Manager: ... (shuaib@acmecorp.com)
...
```

Exact wording varies — Gemini is non-deterministic. Success = tool was called + manager name from DB appears.

---

## 8. Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError: langgraph` | Install LangChain stack in container |
| `GEMINI_API_KEY` missing | Set in `.env`; restart celery |
| Chord never runs callback | All group tasks must succeed; check fetcher errors |
| MCP async context error | Use SSE daemon or stdio with `--transport stdio` |
| `Invalid JSON ... Starting MCP SSE Daemon` | Pass `--transport stdio` in MCP client args; bare `hr_server.py` defaults to SSE |
| `ValueError: contents are required` | Use `create_react_agent` instead of manual `StateGraph` + sync `invoke` — Gemini tool messages need proper formatting |

### stdio vs SSE on the same file

`mcp_daemons/hr_server.py` supports both:

```bash
python mcp_daemons/hr_server.py              # SSE — Docker daemon
python mcp_daemons/hr_server.py --transport stdio   # stdio — subprocess clients
```

Startup logs go to **stderr** in SSE mode so stdout stays JSON-RPC-clean in stdio mode.
| Employee not found | Align `fetch_github_commits` author with real `User.username` |

---

## 9. Interview pitch

> I separate **deterministic I/O from non-deterministic AI reasoning**. A Celery **chord** fans out parallel API fetches across workers. When all context is gathered, the callback boots a **LangGraph** agent with **MCP tools** attached via LangChain adapters. The graph controls flow; Gemini chooses tools; MCP servers query our Django ORM in isolated processes. Phase 4 proved the MCP wire protocol; Phase 5 is the production agent pattern I'd use for incident triage or voice-AI backends at scale.

---

## 10. Conclusion — what this flow proves

If you read the [22-second log autopsy](#0-quick-read--the-log-autopsy-22-seconds) end-to-end, you did not just call an LLM from a script. You built an **enterprise-style autonomous agent pipeline**.

### You just built an enterprise autonomous agent

| What you built | Where it showed up in the run |
|----------------|------------------------------|
| **Multi-node distributed orchestration** | Celery `group` + `chord` over RabbitMQ — three fetchers in parallel, one callback when all complete |
| **Async ↔ sync bridge to legacy data** | MCP tool handler uses `sync_to_async` so FastMCP stays async while Django ORM hits PostgreSQL safely |
| **Cognitive state machine with tool routing** | LangGraph ReAct loop — Gemini decides *when* to call `get_employee_manager`; MCP executes it over stdio JSON-RPC |
| **Resilience without custom retry code** | Gemini returned 503 twice; `langchain-google-genai` backed off and retried — Celery worker kept state and finished |

In one workflow you combined:

1. **Scatter-gather** at the job queue layer (deterministic I/O).
2. **Reasoning + tool selection** at the graph layer (non-deterministic AI).
3. **Standard protocol tool execution** at the MCP layer (isolated process, live DB).
4. **Production-grade fault tolerance** at the LLM client layer (transient API failures absorbed automatically).

That is the pattern teams use for incident triage, internal copilots, and voice-AI backends: **orchestration outside the model, intelligence inside the graph, side effects through MCP.**

Phase 4 proved the wire protocol. Phase 5 proves the **full agent loop** — from cluster fan-out to a manager-ready incident report, with real retries and real database lookups in the path.

---

## Quick reference

| Item | Location |
|------|----------|
| Agent tasks | `apps/incidents/tasks.py` |
| Gemini output parser | `apps/incidents/parser.py` |
| Phase 4 MCP (unchanged) | `apps/organizations/tasks.py` |
| HR MCP server | `mcp_daemons/hr_server.py` |
| Trigger | `trigger_incident_workflow()` |
| Full flow tests | `apps/incidents/tests/test_triage_flow.py` |
| MCP SSE test | `apps/organizations/tests/test_mcp_sse.py` |

---

[← README](../README.md) · [Agent Architecture](AGENT_ARCHITECTURE.md)
