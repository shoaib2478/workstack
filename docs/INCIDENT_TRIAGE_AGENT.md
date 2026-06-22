# Automated Incident Triage Agent — Run & Test Guide

End-to-end guide for the Phase 5 agent: Celery chord → LangGraph → MCP HR lookup.

**Code:** `backend/apps/incidents/tasks.py`

[← Architecture](AGENT_ARCHITECTURE.md) · [LangGraph](LANGGRAPH_DEEP_DIVE.md) · [LangChain + MCP](LANGCHAIN_MCP_INTEGRATION.md)

---

## Table of Contents

1. [What this agent does](#1-what-this-agent-does)
2. [Prerequisites](#2-prerequisites)
3. [Architecture walkthrough — group + chord](#3-architecture-walkthrough)
4. [Line-by-line code map](#4-line-by-line-code-map)
5. [Version A vs Version B](#5-version-a-vs-version-b)
6. [How to test](#6-how-to-test)
7. [Expected output](#7-expected-output)
8. [Troubleshooting](#8-troubleshooting)
9. [Interview pitch](#9-interview-pitch)

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

- Username/email: `shuaib@workstack.dev` (or match `fetch_github_commits` mock author)
- Employee record with a manager in org chart

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

### Step 4 — Optional: test MCP alone first

Before spending Gemini quota:

```bash
docker compose exec web python manage.py test apps.organizations.tests.test_mcp_sse -v 2
```

Proves HR server works independently of LangGraph.

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
| `Invalid JSON ... Starting MCP SSE Daemon` | Agent spawned hr_server without `--transport stdio` — see below |
| `Invalid JSON ... Starting MCP SSE Daemon` | Pass `--transport stdio` in MCP client args; bare `hr_server.py` is SSE-only |
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

## Quick reference

| Item | Location |
|------|----------|
| Agent tasks | `apps/incidents/tasks.py` |
| Phase 4 MCP (unchanged) | `apps/organizations/tasks.py` |
| HR MCP server | `mcp_daemons/hr_server.py` |
| Trigger | `trigger_incident_workflow()` |
| MCP SSE test | `apps/organizations/tests/test_mcp_sse.py` |

---

[← README](../README.md) · [Agent Architecture](AGENT_ARCHITECTURE.md)
