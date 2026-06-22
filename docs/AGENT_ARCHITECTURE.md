# Agent Architecture — Workstack

Where AI agent code lives in the repo, how it relates to Phase 4 MCP, and why `apps/incidents/` is a separate Django app.

[← README](../README.md) · [Incident Triage Agent →](INCIDENT_TRIAGE_AGENT.md)

---

## Table of Contents

1. [Two phases of AI in Workstack](#1-two-phases-of-ai-in-workstack)
2. [Recommended directory layout](#2-recommended-directory-layout)
3. [Why a separate incidents app](#3-why-a-separate-incidents-app)
4. [What stays untouched](#4-what-stays-untouched)
5. [Layer responsibilities](#5-layer-responsibilities)
6. [MCP vs LangGraph vs Celery — who does what](#6-mcp-vs-langgraph-vs-celery--who-does-what)
7. [Architecture rationale](#7-architecture-rationale)

---

## 1. Two phases of AI in Workstack

| Phase | Name | Location | Pattern |
|-------|------|----------|---------|
| **4** | MCP proof-of-loop | `apps/organizations/tasks.py` | Raw Gemini SDK + MCP Client (stdio/SSE) |
| **5** | Incident triage agent | `apps/incidents/tasks.py` | Celery Canvas + LangGraph + LangChain MCP adapter |

Phase 4 implements: **Host → MCP Client → MCP Server → PostgreSQL → Gemini**.

Phase 5 adds: **parallel context fetchers (Celery)** + **stateful agent graph (LangGraph)** + **MCP tools as LangChain tools**.

---

## 2. Recommended directory layout

```
workstack_project/
├── backend/
│   ├── apps/
│   │   ├── organizations/     # Phase 4 — unchanged
│   │   │   ├── tasks.py       # run_ai_org_lookup, send_magic_link_email
│   │   │   └── management/commands/mcp_org_server.py  # stdio dev server
│   │   ├── incidents/         # Phase 5 — incident triage workflows
│   │   │   └── tasks.py       # Celery chord + LangGraph + MCP
│   │   ├── hris/              # Employee data (queried by MCP tools)
│   │   ├── users/
│   │   └── rbac/
│   ├── mcp_daemons/           # Shared tool servers — NOT owned by one app
│   │   └── hr_server.py       # get_employee_manager (SSE :8080)
│   └── core/
├── docs/
└── docker-compose.yml
```

### Rules

| Component | Lives in | Reason |
|-----------|----------|--------|
| **MCP tool servers** | `mcp_daemons/` | Persistent daemons; shared across workflows |
| **Phase 4 learning code** | `organizations/` | Historical reference; do not refactor |
| **Agent orchestration** | `apps/incidents/` | Domain = incident triage; owns Celery canvas |
| **Future MCP servers** | `mcp_daemons/jira_server.py`, etc. | One file per tool domain |

Do **not** put LangGraph code in `organizations/tasks.py`.  
Do **not** add shared MCP servers under `organizations/management/commands/` — use `mcp_daemons/` instead.

---

## 3. Why a separate incidents app

| Reason | Detail |
|--------|--------|
| **Single responsibility** | `organizations` = tenants, invites, membership. `incidents` = triage workflows and agent tasks. |
| **Celery autodiscover** | Tasks in `apps.incidents.tasks` register cleanly; no mixing with invite email tasks |
| **Bounded context** | Incident automation stays separate from org onboarding and HRIS CRUD |
| **Scale later** | Add `Incident` model, API views, and audit logs without touching HRIS code |
| **Testing** | Agent integration tests live beside agent tasks (`apps/incidents/tests/`) |

Alternative considered: one `apps/agents/` app for all future workflows. **`incidents`** matches the first shipped use case (automated incident triage) and keeps the domain name explicit in logs and monitoring.

---

## 4. What stays untouched

| File | Status |
|------|--------|
| `apps/organizations/tasks.py` | Phase 4 — keep as reference |
| `apps/organizations/management/commands/mcp_org_server.py` | stdio dev server |
| `mcp_daemons/hr_server.py` | Shared HR tool server — used by both phases |

Phase 5 **reuses** `hr_server.py` via `MultiServerMCPClient` — it does not duplicate MCP tool logic.

---

## 5. Layer responsibilities

```mermaid
flowchart TB
    subgraph Trigger["Trigger"]
        Shell[Django shell or API]
    end

    subgraph Muscle["Celery Canvas — deterministic"]
        G1[fetch_datadog_metrics]
        G2[fetch_github_commits]
        G3[fetch_slack_alerts]
    end

    subgraph Brain["LangGraph — non-deterministic"]
        Agent[agent node — LLM]
        Tools[execute_tools — ToolNode]
        Agent --> Tools
        Tools --> Agent
    end

    subgraph MCP["MCP layer"]
        Client[MultiServerMCPClient]
        Server[hr_server.py]
        PG[(PostgreSQL)]
    end

    Shell --> Muscle
    G1 --> Chord[chord callback]
    G2 --> Chord
    G3 --> Chord
    Chord --> Brain
    Tools --> Client
    Client --> Server
    Server --> PG
```

| Layer | Technology | Decides what? |
|-------|------------|---------------|
| **Muscle** | Celery `group` + `chord` | *When* to fetch; *parallelism* |
| **Brain** | LangGraph `StateGraph` | *Flow* — loop, stop, retry paths |
| **Reasoning** | Gemini via LangChain | *Which tool* to call; *final text* |
| **Tools** | MCP Server | *How* to query Postgres |

---

## 6. MCP vs LangGraph vs Celery — who does what

| Question | Answer |
|----------|--------|
| Is MCP an "agent"? | No — MCP is **tool execution plumbing** |
| Is LangGraph the agent? | LangGraph is the **orchestration graph**; LLM + tools together form the agent |
| Can you skip LangGraph? | Yes — Phase 4 is a single ReAct loop without a graph |
| Can you skip MCP? | Yes — LangGraph can call plain Python functions as tools |
| Production pattern | **Combine all three** — Celery for I/O, LangGraph for flow, MCP for decoupled tools |

### Why not use MCP for Celery chord fetchers?

Datadog, GitHub, and Slack fetchers in the example are **deterministic Celery tasks** — no LLM needed. MCP is for tools the **agent chooses mid-reasoning** (e.g. lookup manager after reading commit author from logs).

| Data source | Pattern |
|-------------|---------|
| Known upfront, parallel, no AI choice | Celery tasks |
| Agent decides *if* and *when* to query | MCP tools via LangGraph |

---

## 7. Architecture rationale

| Practice | Workstack implementation |
|----------|-------------------------|
| Separate I/O from agent reasoning | Celery chord gathers logs; LangGraph callback runs the agent |
| Shared MCP servers | `mcp_daemons/` — not owned by a single Django app |
| Stable Phase 4 surface | `organizations/` kept for org lookup tasks; no LangGraph there |
| Domain-driven Django apps | `incidents/` owns triage workflows and Celery canvas |
| MCP deployment | SSE daemon in Docker; stdio subprocess from the triage worker |

### Phase 4 vs Phase 5 — when each applies

| | Phase 4 (`organizations/`) | Phase 5 (`incidents/`) |
|---|------------------------------|------------------------|
| **Use case** | Direct org/HR lookup via Gemini + MCP | Incident triage with pre-fetched observability context |
| **Orchestration** | Single Celery task, manual tool loop | Celery `group` + `chord`, then LangGraph ReAct |
| **Flow control** | Gemini SDK tool loop | LangGraph graph (extensible for HITL, routing) |
| **MCP transport** | stdio or SSE | stdio spawn from worker (SSE daemon optional) |

Both phases call the same `mcp_daemons/hr_server.py` tools. Phase 5 does not replace Phase 4 — it adds a workflow layer for multi-step triage.

---

[LangGraph deep dive →](LANGGRAPH_DEEP_DIVE.md) · [LangChain + MCP →](LANGCHAIN_MCP_INTEGRATION.md) · [Run & test →](INCIDENT_TRIAGE_AGENT.md)
