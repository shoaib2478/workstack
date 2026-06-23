# MCP Protocol Gaps — Production Reality & Workstack Contributions

Real limitations teams hit when running MCP outside local IDE demos, how Workstack addresses them, and what we are building next in this repo.

**Related:** [Triage product roadmap](TRIAGE_PRODUCT.md) · [MCP SSE guide](MCP_SSE_HTTP.md) · [Incident triage run](INCIDENT_TRIAGE_AGENT.md)

---

## Table of Contents

1. [Why this document exists](#1-why-this-document-exists)
2. [What Workstack already proved](#2-what-workstack-already-proved)
3. [Gap summary — real flaws vs solved](#3-gap-summary--real-flaws-vs-solved)
4. [Transport: stdio vs SSE (not pooling)](#4-transport-stdio-vs-sse-not-pooling)
5. [Gap A — stdio spawn & local RCE surface](#5-gap-a--stdio-spawn--local-rce-surface)
6. [Gap B — Stateful SSE and horizontal scale](#6-gap-b--stateful-sse-and-horizontal-scale)
7. [Gap C — Large tool responses vs context limits](#7-gap-c--large-tool-responses-vs-context-limits)
8. [Multi-server concurrency](#8-multi-server-concurrency)
9. [Workstack contribution roadmap](#9-workstack-contribution-roadmap)

---

## 1. Why this document exists

MCP tutorials often stop at **one stdio server on a laptop**. Workstack runs MCP through **Celery, LangGraph, PostgreSQL, and persistent SSE daemons** — the same shape as incident triage in production.

This doc separates:

- **Real protocol/deployment gaps** (worth fixing or contributing upstream)
- **Transport mistakes** (stdio per Celery task — fixed by SSE, not a custom pool)
- **What Workstack ships next** ([chunking + live triage UI](TRIAGE_PRODUCT.md))

---

## 2. What Workstack already proved

| Observation | Where we saw it | Mitigation |
|-------------|-----------------|------------|
| stdio spawns Python + `django.setup()` per agent run | Original incidents path | **SSE default** in `mcp_client.py` |
| MCP tool over async boundary | `hr_server.py` | `@sync_to_async` on ORM |
| Gemini 503 under load | ~22s Celery run | `langchain-google-genai` auto-retry |
| Huge inline payloads | Future Datadog/Loki tools | **`chunking.py`** + Redis references |
| Black-box long runs | 20s+ triage | **`events.py`** + SSE live stream |

---

## 3. Gap summary — real flaws vs solved

| ID | Gap | Real? | Workstack status |
|----|-----|-------|------------------|
| **Transport** | stdio MCP server per Celery task | **Yes** — 1–2s boot each run | **Fixed** — `MCP_TRANSPORT=sse` → persistent daemon |
| **A** | stdio shell spawn / trust (local IDEs) | **Yes** on desktop hosts | SSE in prod; fixed command in VPC |
| **B** | Stateful SSE vs load balancers | **Yes** at HA scale | Documented; gateway pattern for later |
| **C** | Huge inline tool/log payloads | **Yes** — main triage hurdle | **`chunking.py` shipped**; MCP read-chunk tool next |
| ~~Pooling~~ | Client↔server HTTP keep-alive | Optimization only on SSE | **Not a flaw** — optional at high QPS |

**Not a protocol flaw:** rewriting MCP servers in C/C++ for I/O-bound APIs (Datadog, GitHub). Network latency dominates.

---

## 4. Transport: stdio vs SSE (not pooling)

### What hurt on stdio

Each Celery task that spawned `hr_server.py --transport stdio` paid:

1. New Python process + `django.setup()`
2. Cold DB pool on the **server** side
3. Full MCP handshake

That is the problem people describe as needing "PgBouncer for MCP." The fix is **not** a custom client↔server connection pool on top of stdio — **stdio processes cannot be pooled meaningfully**.

### What SSE fixes

| Piece | stdio per task | SSE daemon |
|-------|----------------|------------|
| MCP server | Spawn + exit | **24/7 ASGI process** |
| Server → Postgres pool | Cold each time | **Warm in daemon** |
| Client per task | New subprocess pipes | **HTTP connect + MCP session** (ms, not seconds) |

Celery **child processes stay alive** across tasks, but the **MCP client inside each task** still opens and closes per run — that is **acceptable** on SSE. HTTP keep-alive across tasks is an optional throughput tweak, not a gap.

### Workstack default today

```python
# apps/incidents/mcp_client.py — MCP_TRANSPORT=sse (default)
MultiServerMCPClient({
    "workstack_hr": {"url": MCP_SSE_URL, "transport": "sse"},
})
```

Set `MCP_TRANSPORT=stdio` only when the SSE daemon is not running.

Requires: `docker compose up mcp_hr_daemon`

---

## 5. Gap A — stdio spawn & local RCE surface

stdio MCP hosts run **shell commands from config** (Cursor, Claude Desktop). Untrusted servers or prompt injection → arbitrary local execution.

**Workstack production path:** fixed SSE daemon in Docker, no marketplace spawn in the worker VPC.

---

## 6. Gap B — Stateful SSE and horizontal scale

Long-lived SSE streams break on naive round-robin deploys. Enterprises accept gateway/session layers for HA. Workstack runs a **single replica** today; gateway design is documented for scale-out later.

---

## 7. Gap C — Large tool responses vs context limits

### The flaw

MCP returns **full inline text** for logs, queries, and file reads. Multi-MB payloads break LLM context and cost.

### Workstack fix (shipped)

`apps/incidents/chunking.py`:

- Inline cap: `TRIAGE_MAX_INLINE_CHARS`
- Overflow → Redis `triage:ref:<id>` + `[TRIAGE_REF ...]` marker in prompt
- Next: MCP tool `read_triage_chunk(ref_id, index)` for agent-driven pull

See [TRIAGE_PRODUCT.md](TRIAGE_PRODUCT.md) §3 and [INCIDENT_TRIAGE_QA.md](INCIDENT_TRIAGE_QA.md) §4.

---

## 8. Multi-server concurrency

| Transport | 5 parallel tool calls |
|-----------|----------------------|
| stdio | 1 Celery child + **5 MCP subprocesses** |
| SSE | 1 Celery child + **5 HTTP sessions** to running daemons |

Parallel **known** fetchers → Celery `group`. Parallel **agent-chosen** tools → async MCP inside the triage task.

---

## 9. Workstack contribution roadmap

| Priority | Item | Status |
|----------|------|--------|
| **P0** | SSE in LangGraph incidents path | **Done** — `mcp_client.py` |
| **P0** | Log chunking for LLM context | **Done** — `chunking.py` |
| **P0** | Live triage checkpoints (SSE API) | **Done** — `events.py`, `views.py` |
| **P1** | `read_triage_chunk` MCP tool | Planned |
| **P1** | Configurable Datadog/Grafana fetchers | Planned |
| **P2** | React live triage panel | Planned |
| **P2** | Slack MCP notification server | Planned |
| **P3** | Stateless MCP gateway (Gap B) | Design |

---

## Quick reference

| Topic | Location |
|-------|----------|
| Product roadmap + live UI | [TRIAGE_PRODUCT.md](TRIAGE_PRODUCT.md) |
| MCP client factory | `apps/incidents/mcp_client.py` |
| Chunking | `apps/incidents/chunking.py` |
| Live stream | `GET /api/v1/incidents/runs/<run_id>/stream/` |
| SSE daemon | `mcp_daemons/hr_server.py` |

---

[← README](../README.md) · [Agent Architecture](AGENT_ARCHITECTURE.md)
