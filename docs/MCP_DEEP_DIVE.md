# MCP Deep Dive: Architecture, Workflow, and FAQ

This document expands on the [main README](../README.md) with the concepts, transports, and common questions around Model Context Protocol (MCP) — adapted for the Workstack HRIS codebase.

---

## Table of Contents

1. [Why MCP Exists](#1-why-mcp-exists)
2. [Core Architecture](#2-core-architecture)
3. [Who Connects to Whom?](#3-who-connects-to-whom)
4. [End-to-End Workflow (stdio)](#4-end-to-end-workflow-stdio)
5. [Transports: stdio vs HTTP/SSE](#5-transports-stdio-vs-httpsse)
6. [LangChain vs MCP (Simple Examples)](#6-langchain-vs-mcp-simple-examples)
7. [Real-World Usage in Workstack](#7-real-world-usage-in-workstack)
8. [FAQ: Common Confusions](#8-faq-common-confusions)
9. [Known Gaps and Side-Project Ideas](#9-known-gaps-and-side-project-ideas)
10. [Workstack: Line-by-Line Flow](#10-workstack-line-by-line-flow)
11. [Running and Troubleshooting](#11-running-and-troubleshooting)

---

## 1. Why MCP Exists

### The N × M Problem

Connecting AI apps to external tools traditionally meant **custom integrations everywhere**:

- *M* AI hosts (Celery workers, Cursor, internal portals)
- *N* tools (PostgreSQL HR data, Slack, Jira)

Without a standard, you build up to **M × N** pipelines. MCP introduces one protocol on each side, reducing complexity toward **M + N** — each host implements MCP once, each tool exposes an MCP server once.

### Why MCP Even for a Single Agent?

MCP is not only about ecosystem scale. It also gives you:

| Benefit | What it means in practice |
|---------|---------------------------|
| **Decoupled deployment** | Update HR tool logic without redeploying Celery orchestration |
| **Dynamic discovery** | Server advertises current tools at runtime |
| **Process isolation** | Tool code crashes in a subprocess/daemon, not inside Gunicorn |
| **Boundary for policy** | Rate limits, audit logs, human approval at the client–server bridge |
| **Language agnostic** | Server in Python, host in Python — they only speak JSON-RPC |

---

## 2. Core Architecture

MCP is built on **JSON-RPC 2.0** messages over a transport layer.

```
[ MCP HOST ]          e.g. Celery task (run_ai_org_lookup), Cursor
      │
      │  owns LLM API + spawns/manages clients
      ▼
[ MCP CLIENT ]        ClientSession (mcp Python SDK)
      │
      │  stdin/stdout (local)  OR  HTTP + SSE (remote)
      ▼
[ MCP SERVER ]        FastMCP wrapper (mcp_org_server.py / hr_server.py)
      │
      ▼
[ External system ]   PostgreSQL via Django ORM
```

### Three primitives servers expose

| Primitive | Purpose | Workstack example |
|-----------|---------|-------------------|
| **Resources** | Read-only context | (future) org schema, policy docs |
| **Tools** | Callable functions | `get_employee_manager(email)` |
| **Prompts** | Instruction templates | (future) expense approval prompt |

---

## 3. Who Connects to Whom?

```
┌────────────────────────────────────────────────────────┐
│                      THE HOST                          │
│              (Celery task + Gemini client)               │
│                                                        │
│  ┌───────────┐      Text / JSON      ┌──────────────┐  │
│  │ Gemini API│ ◄───────────────────► │  MCP Client  │  │
│  └───────────┘                       └──────┬───────┘  │
└─────────────────────────────────────────────┼──────────┘
                                              │ stdio / SSE
                                              ▼
                                       ┌──────────────┐
                                       │  MCP Server  │
                                       └──────────────┘
```

| Entity | Connected to | Not connected to |
|--------|--------------|------------------|
| **LLM (Gemini)** | Host (via API) | MCP Client, MCP Server |
| **Host** | LLM + MCP Client(s) | Server directly (always via client) |
| **MCP Client** | Host + one MCP Server | LLM |
| **MCP Server** | MCP Client | LLM |

**The Host is the orchestrator.** It:

1. Sends user prompts to Gemini
2. Injects tool definitions (hand-mapped or discovered)
3. **Intercepts** function calls from Gemini
4. Delegates execution to the MCP Client
5. Feeds tool results back to Gemini for a natural-language reply

---

## 4. End-to-End Workflow (stdio)

Workstack's Celery task uses **local stdio transport**: the host spawns `mcp_org_server.py` as a child process and communicates over OS pipes.

### Phase 1: Initialization

| Step | Actor | Action |
|------|-------|--------|
| 1 | Celery worker | Receives `run_ai_org_lookup` from RabbitMQ |
| 2 | Host | Spawns `python mcp_org_server.py` subprocess |
| 3 | Subprocess | Calls `django.setup()` — loads Django registry + DB pool |
| 4 | Client | Sends JSON-RPC `initialize` on **stdin** |
| 5 | Server | Replies on **stdout** with protocol version + capabilities |
| 6 | Client | Sends `initialized` notification |

### Phase 2: Tool Discovery

| Step | Actor | Action |
|------|-------|--------|
| 1 | Host | Attaches `GET_MANAGER_TOOL` schema to Gemini request |
| 2 | Client | May call `tools/list` (SDK-dependent) |
| 3 | Server | Returns `get_employee_manager` with `email: string` |

**Note:** Workstack currently hand-writes the Gemini `FunctionDeclaration` in `tasks.py` because the Gemini SDK uses a different schema format than FastMCP's auto-generated MCP schema. The parameter names must align or Gemini may refuse to call the tool.

### Phase 3: Execution Loop (double-turn handshake)

| Step | Actor | Action |
|------|-------|--------|
| 1 | Host | Prompt: "Find the manager for shuaib@workstack.dev …" |
| 2 | Gemini | Returns `get_employee_manager(email="shuaib@workstack.dev")` |
| 3 | Host | `mcp_session.call_tool(...)` → stdin/stdout to subprocess |
| 4 | Server | `Employee.objects.select_related('manager').get(email=...)` |
| 5 | Host | Sends tool result back to Gemini (Turn 2) |
| 6 | Gemini | Natural-language answer: "Contact Alice (VP) at …" |

---

## 5. Transports: stdio vs HTTP/SSE

### stdio (Standard Input / Output)

**stdio** = standard I/O streams every OS process has:

| Stream | Direction | MCP usage |
|--------|-----------|-----------|
| **stdin** | Into the process | Client writes JSON-RPC requests |
| **stdout** | Out of the process | Server writes JSON-RPC responses |
| **stderr** | Logs (not protocol) | Debug output; must not break JSON parsing |

When the MCP client runs locally, it **spawns** the server as a subprocess. No HTTP, no open ports — just text over pipes.

Workstack stdio server: `apps/organizations/management/commands/mcp_org_server.py`

```python
server_params = StdioServerParameters(
    command="python",
    args=[server_path]
)
async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
```

**Production caveat:** Spawning a subprocess with `django.setup()` on **every Celery task** adds a 1–2 second boot penalty (Python import + DB connection). Fine for dev; avoid at scale.

### HTTP + SSE (Server-Sent Events)

For **persistent** MCP servers (local testing or production):

- **HTTP POST** — client sends actions (`initialize`, `tools/call`)
- **SSE** — server keeps a one-way stream open to push events and responses

Workstack SSE daemon: `backend/mcp_daemons/hr_server.py`

```python
# django.setup() runs ONCE at process start
mcp.run(transport="sse", host="0.0.0.0", port=8080)
```

Docker service `mcp_hr_daemon` exposes port **8080**. Celery workers connect via HTTP instead of spawning subprocesses — warm connection pool, no repeated Django boot.

| Transport | Boot cost | Isolation | Best for |
|-----------|-----------|-----------|----------|
| **stdio** | Per spawn | Strong (subprocess) | Local dev, one-off scripts |
| **SSE/HTTP** | Once at daemon start | Service boundary | Production, shared tool fleet |

---

## 6. LangChain vs MCP (Simple Examples)

### Traditional: tools live inside the app

```python
# Everything in ONE process — Celery imports HR logic directly
@tool
def get_manager(email: str) -> str:
    employee = Employee.objects.get(email=email)
    return employee.manager.email
```

Changing the tool means changing and redeploying the orchestrator.

### MCP: tools live in a separate process

```python
# Host does NOT import Employee model for tool execution
async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(
            "get_employee_manager",
            arguments={"email": "shuaib@workstack.dev"}
        )
```

The host only knows a **command to spawn** (or an **HTTP URL**) and a **protocol**.

---

## 7. Real-World Usage in Workstack

**Scenario:** When inviting a user, an async task needs to look up an employee's manager for expense approval routing.

| Component | Role |
|-----------|------|
| `InviteUserService` | Queues `send_magic_link_email` via Celery |
| `run_ai_org_lookup` | (Experimental) Gemini + MCP manager lookup |
| `get_employee_manager` | MCP tool querying live `Employee` rows |

**Without MCP:** Gemini integration code embeds Django ORM queries directly in the Celery task — tight coupling, hard to test in isolation.

**With MCP:** HR query logic lives in `mcp_org_server.py` or `hr_server.py`. The Celery task only orchestrates LLM + protocol. Swap the server implementation (stdio → SSE) without touching Gemini prompt logic.

---

## 8. FAQ: Common Confusions

### Is one client always connected to one server?

**Yes.** One `ClientSession` ↔ one server for its lifetime. Need HR + payroll tools? Create **two clients** (or two servers behind one gateway).

### When is the MCP client created?

When the host opens a transport (`stdio_client(...)` or SSE URL). In Workstack, that happens inside `run_mcp_agent_loop()` at the start of each Celery task (stdio path).

### Does Gemini ask for `tools/list`?

No. The **host** provides tool definitions before the model decides to call them. Gemini only sees what the host injects via `GenerateContentConfig(tools=[...])`.

### Who intercepts the LLM tool call?

The **Host** (`run_mcp_agent_loop`). It checks `response.function_calls`, then calls `mcp_session.call_tool(...)`.

### What is stderr for?

Server logs and debug prints should go to **stderr**, not stdout. stdout is reserved for JSON-RPC.

### Why did Gemini say "I need an employee ID"?

The hand-written Gemini schema did not match what the model expected, or tool calling mode was `AUTO` instead of `ANY`. Workstack forces a tool call with:

```python
types.ToolConfig(
    function_calling_config=types.FunctionCallingConfig(
        mode=types.FunctionCallingConfigMode.ANY,
        allowed_function_names=["get_employee_manager"]
    )
)
```

### Should MCP servers live inside a Django app?

**No for production SSE daemons.** See [MCP_INTEGRATION.md](MCP_INTEGRATION.md) — persistent servers belong in `backend/mcp_daemons/`, parallel to `apps/`, not buried under `organizations/`.

---

## 9. Known Gaps and Side-Project Ideas

| Gap | Side-project idea |
|-----|-------------------|
| No standard auth across MCP servers | OAuth2 proxy with per-tool scopes |
| 1:1 client–server | Connection multiplexer for tool fleets |
| Large tool results | Compress/summarize before Gemini Turn 2 |
| Duplicate schemas (MCP → Gemini) | Auto-adapter: MCP schema → google-genai `types.Tool` |
| stdio boot penalty | SSE daemon (already started in Workstack) |
| Observability | OpenTelemetry traces on every `tools/call` |

---

## 10. Workstack: Line-by-Line Flow

### stdio Server: `mcp_org_server.py`

```python
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local")
django.setup()

from apps.hris.models import Employee

mcp = FastMCP("Workstack_Org_Chart")

@mcp.tool()
def get_employee_manager(email: str) -> str:
    employee = Employee.objects.select_related('manager').get(email=email.lower())
    ...
```

When run as a subprocess, `mcp.run()` (default stdio) reads JSON from stdin and writes responses to stdout.

### SSE Daemon: `hr_server.py`

Same tool definition, but:

```python
mcp.run(transport="sse", host="0.0.0.0", port=8080)
```

`django.setup()` runs **once** when the container starts.

### Host: `tasks.py` (`run_mcp_agent_loop`)

1. **`genai.Client()`** — Host owns the Gemini connection.
2. **`StdioServerParameters(...)`** — Path to `mcp_org_server.py`.
3. **`stdio_client` + `ClientSession`** — MCP client over pipes.
4. **`session.initialize()`** — Handshake.
5. **Turn 1** — Prompt + `GET_MANAGER_TOOL` + `FunctionCallingConfigMode.ANY`.
6. **`session.call_tool(...)`** — Execute via MCP subprocess.
7. **Turn 2** — Feed function response back; mode `AUTO` for natural language.

### Celery entrypoint

```python
@shared_task(name="apps.organizations.tasks.run_ai_org_lookup")
def run_ai_org_lookup(target_email):
    return asyncio.run(run_mcp_agent_loop(target_email))
```

---

## 11. Running and Troubleshooting

### Prerequisites

```bash
# Inside web/celery container or local venv
pip install -r backend/requirements/base.txt
export GEMINI_API_KEY="your_key"
```

### Trigger from Django shell

```python
from apps.organizations.tasks import run_ai_org_lookup
run_ai_org_lookup.delay("employee@workstack.dev")
```

### Start SSE daemon locally

```bash
# Terminal 1
docker compose up mcp_hr_daemon

# Or directly
python backend/mcp_daemons/hr_server.py
# → Starting MCP SSE Daemon on port 8080...
```

### What to watch in Celery logs

1. **Task received** — RabbitMQ delivery
2. **Subprocess spawned** (stdio) or **HTTP connect** (SSE) — MCP session
3. **Turn 1** — Gemini function call
4. **Tool result** — Manager string from PostgreSQL
5. **Turn 2** — Final Gemini paragraph

### Troubleshooting

| Issue | Check |
|-------|-------|
| `GEMINI_API_KEY` missing | Set in `.env`, restart celery |
| Django import error in subprocess | `DJANGO_SETTINGS_MODULE` and `sys.path` in server script |
| Gemini refuses tool | Use `FunctionCallingConfigMode.ANY`; align parameter names |
| Empty tool result | Employee email exists in DB; check org/HRIS data |
| Garbled JSON on stdio | Move `print()` to stderr in MCP server |
| SSE connection refused | `mcp_hr_daemon` running on 8080 |

---

## Further Reading

- [MCP Integration Guide (Workstack)](MCP_INTEGRATION.md) — file layout, production architecture, Docker
- [Model Context Protocol specification](https://modelcontextprotocol.io/)
- [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Main README](../README.md)

---

[← Back to README](../README.md)
