# MCP Integration Guide — Workstack

This guide covers **where MCP servers live in the repo**, how **Django initialization** works in subprocess vs daemon mode, the **Celery + Gemini loop**, and how to run **stdio (dev)** vs **SSE/HTTP (production-style)** locally.

For protocol concepts and FAQ, see [MCP_DEEP_DIVE.md](MCP_DEEP_DIVE.md).

---

## Table of Contents

1. [The Django Initialization Problem](#1-the-django-initialization-problem)
2. [Where Should MCP Servers Live?](#2-where-should-mcp-servers-live)
3. [Step 1: stdio MCP Server (Development)](#3-step-1-stdio-mcp-server-development)
4. [The Host → Client → Server Flow (Line-by-Line)](#4-the-host--client--server-flow-line-by-line)
5. [Step 2: Celery Task with Gemini Loop](#5-step-2-celery-task-with-gemini-loop)
6. [Why Gemini Needs the Tool Schema](#6-why-gemini-needs-the-tool-schema)
7. [ToolConfig: Forcing Tool Calls & Error Handling](#7-toolconfig-forcing-tool-calls--error-handling)
8. [Debugging: When Gemini Refuses Email Lookup](#8-debugging-when-gemini-refuses-email-lookup)
9. [Step 3: Trigger via RabbitMQ](#9-step-3-trigger-via-rabbitmq)
10. [Step 4: SSE Daemon (Production-Style)](#10-step-4-sse-daemon-production-style)
11. [stdio vs SSE: Decision Matrix](#11-stdio-vs-sse-decision-matrix)
12. [Docker Production Setup](#12-docker-production-setup)
13. [Next Steps (Not Yet Implemented)](#13-next-steps-not-yet-implemented)

---

## 1. The Django Initialization Problem

Celery workers run in an environment where **Django is already initialized** — models, settings, and DB connections are ready.

An MCP server running as a **stdio subprocess** is a **separate Python process**. It cannot import `Employee` or query PostgreSQL until you explicitly boot Django:

```python
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local")
django.setup()
```

Without this, any `@mcp.tool()` that touches the ORM will fail with `AppRegistryNotReady` or import errors.

**Key insight:** In stdio mode, `django.setup()` runs **every time** Celery spawns the subprocess — typically 1–2 seconds of overhead per task.

---

## 2. Where Should MCP Servers Live?

### Do not put all MCP servers inside a Django app

A persistent SSE MCP server is its own web process (like Gunicorn). Burying it under `apps/organizations/management/commands/` makes containerization and scaling awkward.

### Recommended layout

```
workstack_project/
├── backend/
│   ├── apps/                    # Django apps (hris, organizations, users, rbac)
│   ├── core/                    # settings, wsgi, celery
│   ├── mcp_daemons/             # Persistent SSE MCP servers
│   │   ├── __init__.py
│   │   └── hr_server.py         # Org chart / HR tools
│   └── apps/organizations/
│       └── management/commands/
│           └── mcp_org_server.py  # stdio subprocess (dev / Celery spawn)
```

| File | Transport | Purpose |
|------|-----------|---------|
| `mcp_org_server.py` | stdio (default `mcp.run()`) | Spawned by Celery; good for dev and proving the loop |
| `mcp_daemons/hr_server.py` | SSE on `:8080` | Long-running daemon; `django.setup()` once |

**Rule of thumb:**

- **One-off / subprocess tools** → management command or dedicated script invoked by path
- **Shared / production tools** → `mcp_daemons/` as separate Docker services

When you add more domains (payroll, time-off), add `payroll_server.py` under `mcp_daemons/` rather than nesting under random apps.

---

## 3. Step 1: stdio MCP Server (Development)

**File:** `backend/apps/organizations/management/commands/mcp_org_server.py`

```python
import os
import sys
import django
from mcp.server.fastmcp import FastMCP

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../..")
)
sys.path.append(PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local")
django.setup()

from apps.hris.models import Employee

mcp = FastMCP("Workstack_Org_Chart")

@mcp.tool()
def get_employee_manager(email: str) -> str:
    """Finds the manager's name and email for a given employee email."""
    try:
        employee = Employee.objects.select_related('manager').get(email=email.lower())
        if employee.manager:
            return f"Manager: {employee.manager.first_name} {employee.manager.last_name} ({employee.manager.email})"
        return "This employee has no assigned manager (likely top-level tier)."
    except Employee.DoesNotExist:
        return f"Error: No employee found with email {email}."

if __name__ == "__main__":
    mcp.run()  # stdio — listens on stdin/stdout
```

**Test manually:**

```bash
python backend/apps/organizations/management/commands/mcp_org_server.py
# Process waits on stdin (MCP client required to interact)
```

Use **stderr** for debug logging — never `print()` to stdout in production stdio servers.

---

## 4. The Host → Client → Server Flow (Line-by-Line)

Map every layer in `tasks.py` so the mental model clicks. There are **four actors**, not three — the OS kernel spawns the Server before the Client talks to it.

```
┌─────────────────────────────────────────────────────────────────┐
│  HOST (Celery worker process)                                   │
│  run_mcp_agent_loop() — holds GEMINI_API_KEY, orchestrates all  │
│                                                                 │
│   ┌─────────────┐         ┌──────────────────┐                  │
│   │ Gemini API  │◄───────►│  MCP Client      │                  │
│   │ (Google)    │  HTTPS  │  ClientSession   │                  │
│   └─────────────┘         └────────┬─────────┘                  │
└────────────────────────────────────┼────────────────────────────┘
                                     │ read/write OS pipes (stdio)
                                     ▼
                          ┌──────────────────────┐
                          │  MCP SERVER          │
                          │  mcp_org_server.py   │
                          │  django.setup() + ORM│
                          └──────────┬───────────┘
                                     ▼
                               PostgreSQL
```

### 1. The Host (The Brain)

**Code:** `ai_client.models.generate_content(...)`

**File:** `backend/apps/organizations/tasks.py`

The Celery task `run_mcp_agent_loop` is the **Host**. It:

- Holds the Gemini API key
- Builds the user prompt and tool schema (`GET_MANAGER_TOOL`)
- Sends Turn 1 to Google over the internet
- Intercepts `response.function_calls`
- Sends Turn 2 after the tool runs

The Host is the **only** component that talks to the LLM. The MCP Server never sees Gemini.

```python
response = ai_client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt,
    config=types.GenerateContentConfig(
        tools=[GET_MANAGER_TOOL],
        tool_config=enforcer_config,
    ),
)
```

### 2. Spawning the Server (The Hands)

**Code:** `async with stdio_client(server_params) as (read, write):`

**What happens:** Before Gemini is called, the Host asks the Linux kernel to **fork a new Python subprocess** running `mcp_org_server.py`. That subprocess is the **Server**. It:

1. Calls `django.setup()`
2. Opens a PostgreSQL connection pool
3. Enters `mcp.run()` and waits on **stdin**

The Host receives two OS pipes: `read` (Server → Host) and `write` (Host → Server).

```python
server_params = StdioServerParameters(
    command="python",
    args=[server_path],  # .../mcp_org_server.py
)
async with stdio_client(server_params) as (read, write):
    ...
```

> **Important:** The Server is alive and connected to Postgres **before** Turn 1 hits Gemini. But Gemini does not know that yet — it only sees the tool schema the Host sends.

### 3. The Client (The Nervous System)

**Code:** `async with ClientSession(read, write) as mcp_session:`

The Host does **not** write raw JSON to the pipes itself. The **MCP Client** (`ClientSession`) is a protocol adapter inside the Host process. It:

- Performs the MCP handshake (`await mcp_session.initialize()`)
- Translates Python calls into JSON-RPC 2.0 on the write pipe
- Parses JSON-RPC responses from the read pipe

```python
async with ClientSession(read, write) as mcp_session:
    await mcp_session.initialize()
```

The Client is **blind to business logic**. It does not know what a manager is — only how to send `tools/call`.

### 4. Execution Routing (When Gemini Returns a Function Call)

**Code:** `await mcp_session.call_tool(tool_call.name, tool_call.args)`

| Step | Actor | Action |
|------|-------|--------|
| 1 | Host | Reads `tool_call.name` and `tool_call.args` from Gemini's response |
| 2 | Client | Wraps them in JSON-RPC `tools/call` and writes to the **write pipe** |
| 3 | Server | Reads stdin, runs `get_employee_manager(email=...)`, queries PostgreSQL |
| 4 | Server | Writes result string (e.g. `"Manager: Alice (...)"`) to **stdout** |
| 5 | Client | Parses JSON-RPC response, returns `mcp_result` to the Host |
| 6 | Host | Builds Turn 2 with `Part.from_function_response(...)` and calls Gemini again |

```python
if response.function_calls:
    tool_call = response.function_calls[0]
    mcp_result = await mcp_session.call_tool(tool_call.name, tool_call.args)
    tool_output_text = mcp_result.content[0].text
    # Host sends tool_output_text back to Gemini for the final paragraph
```

### Why this separation matters

If you swap Gemini for Claude tomorrow, you change **only the Host** (`generate_content` → Anthropic API). The Client and Server are LLM-agnostic — they only speak MCP JSON-RPC.

---

## 5. Step 2: Celery Task with Gemini Loop

**File:** `backend/apps/organizations/tasks.py`

The task implements a **double-turn handshake**:

1. **Turn 1** — Ask Gemini with tools; force `get_employee_manager` via `FunctionCallingConfigMode.ANY`.
2. **Execute** — `mcp_session.call_tool(...)` hits the stdio subprocess.
3. **Turn 2** — Send function call + function response back to Gemini; mode `AUTO` for natural language.

```python
async def run_mcp_agent_loop(target_email: str):
    ai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    server_path = os.path.join(
        settings.BASE_DIR,
        "apps", "organizations", "management", "commands", "mcp_org_server.py"
    )
    server_params = StdioServerParameters(command="python", args=[server_path])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()
            # ... Turn 1: generate_content with tools + ANY mode
            # ... call_tool via MCP
            # ... Turn 2: generate_content with function response
```

**Why hand-write `GET_MANAGER_TOOL`?**

FastMCP auto-generates MCP JSON schemas from Python signatures. The Gemini SDK expects `google.genai.types.Tool`. The host must translate (or duplicate) schemas so parameter names like `email` match on both sides.

### Full Turn 1 + Turn 2 reference (`tasks.py`)

```python
# Turn 1 — force tool execution (see Section 7)
enforcer_config = types.ToolConfig(
    function_calling_config=types.FunctionCallingConfig(
        mode=types.FunctionCallingConfigMode.ANY,
        allowed_function_names=["get_employee_manager"],
    )
)

response = ai_client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt,
    config=types.GenerateContentConfig(
        tools=[GET_MANAGER_TOOL],
        tool_config=enforcer_config,
    ),
)

if response.function_calls:
    tool_call = response.function_calls[0]
    mcp_result = await mcp_session.call_tool(tool_call.name, tool_call.args)

    # Turn 2 — natural language summary (mode AUTO)
    final_response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
            types.Content(role="model", parts=[types.Part.from_function_call(
                name=tool_call.name, args=tool_call.args,
            )]),
            types.Content(role="user", parts=[types.Part.from_function_response(
                name=tool_call.name,
                response={"result": mcp_result.content[0].text},
            )]),
        ],
        config=types.GenerateContentConfig(
            tools=[GET_MANAGER_TOOL],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.AUTO,
                )
            ),
        ),
    )
    return final_response.text

# Fallback when no function call was returned (should not happen with ANY mode)
return response.text
```

---

## 6. Why Gemini Needs the Tool Schema

This is the most common architectural confusion:

> "The Client connects to the Server. Why does Google/Gemini need to know the parameters?"

### The restaurant analogy

| Role | MCP equivalent | What they know |
|------|----------------|----------------|
| **Customer** | Your prompt | "Find the manager for katrina@newhire.com" |
| **Chef** | Gemini | Writes the order ticket — must specify *what* to fetch |
| **Waiter** | MCP Client | Walks to the pantry, but is blind — needs a written order |
| **Pantry** | MCP Server | Has the ingredients (PostgreSQL), executes the order |

The Waiter (Client) knows **how** to reach the Server (pipes or HTTP). But the Waiter does not decide **what** to query. **Gemini writes the JSON dictionary:**

```json
{"email": "katrina@newhire.com"}
```

That dictionary is handed to the Client as `tool_call.args`. The Client forwards it to the Server via JSON-RPC. If Gemini never received the schema, it could not produce valid `args` — it would only guess or reply in plain text.

### Two separate schema paths (easy to confuse)

```
FastMCP Server                    Gemini API
─────────────────                 ─────────────────────────────
@mcp.tool()                       GET_MANAGER_TOOL in tasks.py
  ↓ auto-generates                  ↓ hand-written by you
MCP tools/list schema             types.FunctionDeclaration
  ↓ used by Client only             ↓ sent to Google in Turn 1
mcp_session.call_tool(...)        response.function_calls[0].args
```

| Schema | Who consumes it | When |
|--------|-----------------|------|
| FastMCP / MCP `tools/list` | MCP Client ↔ Server | During `call_tool` |
| `GET_MANAGER_TOOL` (Gemini format) | Google Gemini API | During `generate_content` |

**Gemini never reads the MCP server's auto-generated schema.** It only sees what the Host puts in `GenerateContentConfig(tools=[...])`. That is why a mismatch between `GET_MANAGER_TOOL` and your Python function signature causes silent failures.

### Duplicate schemas are intentional (for now)

Until an auto-adapter exists, you maintain:

1. **Server:** `def get_employee_manager(email: str)` — FastMCP → MCP JSON schema
2. **Host:** `GET_MANAGER_TOOL` — Gemini `FunctionDeclaration`

Parameter names (`email`), types (`STRING`), and `required` fields must align on both sides.

---

## 7. ToolConfig: Forcing Tool Calls & Error Handling

The Google GenAI SDK exposes `ToolConfig` to control **when** the model may call tools vs reply in text. This is separate from MCP — it governs Gemini's behavior only.

### FunctionCallingConfigMode values

| Mode | Behavior | Use in Workstack |
|------|----------|------------------|
| `AUTO` (default) | Model decides: text reply **or** tool call | Turn 2 — let Gemini summarize the manager name |
| `ANY` | Model **must** call one of the allowed tools; text-only replies blocked | Turn 1 — force lookup, prevent "I need an employee ID" |
| `NONE` | Tools visible but model cannot call them | Rare; debugging or disabling tools |

### Turn 1: Force the tool (`ANY`)

Without `ANY`, Gemini often returns plain text instead of `function_calls` — especially when its training says HR lookups need an `employee_id`:

```python
enforcer_config = types.ToolConfig(
    function_calling_config=types.FunctionCallingConfig(
        mode=types.FunctionCallingConfigMode.ANY,
        allowed_function_names=["get_employee_manager"],  # restrict to one tool
    )
)
```

`allowed_function_names` is optional but recommended when you pass multiple tools — it prevents Gemini from calling the wrong one.

### Turn 2: Allow natural language (`AUTO`)

After the Server returns data, switch to `AUTO` so Gemini writes a human-readable answer instead of trying to call the tool again:

```python
tool_config=types.ToolConfig(
    function_calling_config=types.FunctionCallingConfig(
        mode=types.FunctionCallingConfigMode.AUTO,
    )
)
```

### Handling errors at each layer

| Layer | Failure | Symptom | Handling |
|-------|---------|---------|----------|
| **Host → Gemini** | Missing `GEMINI_API_KEY` | API exception | Catch in Celery task; log and retry |
| **Host → Gemini** | `ANY` not set | `response.function_calls` empty; text like "I need an employee ID" | Add `ToolConfig` with `ANY` (Section 8) |
| **Host → Gemini** | Bad `GET_MANAGER_TOOL` schema | Tool call with wrong/missing args | Align schema with server signature |
| **Client → Server** | Subprocess crash | `call_tool` raises | Log stderr from server; check `django.setup()` |
| **Client → Server** | JSON on stdout corrupted | Parse error | Move all `print()` to stderr on server |
| **Server → DB** | Employee not found | Tool returns error string | Pass to Gemini Turn 2; model explains to user |
| **Turn 2** | Empty `final_response.text` | Blank Celery result | Check function_response format; ensure `result` key |

### Recommended Host error handling pattern

```python
async def run_mcp_agent_loop(target_email: str):
    if not os.environ.get("GEMINI_API_KEY"):
        logger.error("mcp_agent_missing_api_key")
        return "Error: GEMINI_API_KEY not configured."

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as mcp_session:
                await mcp_session.initialize()

                response = ai_client.models.generate_content(...)

                if not response.function_calls:
                    # ANY mode should prevent this — log for debugging
                    logger.warning(
                        "mcp_agent_no_function_call",
                        gemini_text=response.text,
                        target_email=target_email,
                    )
                    return response.text or "No tool call returned."

                tool_call = response.function_calls[0]

                try:
                    mcp_result = await mcp_session.call_tool(
                        tool_call.name, tool_call.args
                    )
                    tool_output_text = mcp_result.content[0].text
                except Exception as exc:
                    logger.error("mcp_call_tool_failed", error=str(exc))
                    tool_output_text = f"Tool error: {exc}"

                final_response = ai_client.models.generate_content(...)
                return final_response.text or tool_output_text

    except Exception as exc:
        logger.error("mcp_agent_loop_failed", error=str(exc))
        raise
```

### Apply ToolConfig to SSE path too

`run_mcp_agent_loop_sse` in `tasks.py` currently calls Gemini with default `AUTO` only. For consistent behavior, add the same `enforcer_config` to Turn 1 and `AUTO` to Turn 2 — identical to the stdio Host.

---

## 8. Debugging: When Gemini Refuses Email Lookup

### The error you saw

```
I can't find a manager using just an email address. I need an employee ID or user ID.
Is there another way I can help?
```

Or:

```
The get_employee_manager tool does not accept an email address as a parameter.
It requires an employee ID or other identifiers like department, job_title, or user_id.
```

Celery logs this as a **successful task** because Gemini returned valid text — the MCP Server was **never called**.

### Root cause: Semantic prior (not an MCP bug)

Gemini was trained on billions of HR/IT documents where employee lookup almost always uses `employee_id` or `user_id`. When it sees your prompt + tool schema:

1. Its **training prior** says: "HR DB lookups need an ID."
2. With `AUTO` mode, it **chooses** to reply in text instead of calling the tool.
3. It may even **hallucinate** that your tool rejects email — without ever calling it.

The MCP Client and Server are innocent. Gemini never sent an order ticket to the Waiter.

### Prompt engineering alone is unreliable

This prompt still fails under `AUTO`:

```python
prompt = (
    f"Can you find out who I need to contact to approve expenses for {target_email}? "
    f"You MUST use this tool when provided with an email address. Do not ask for an ID."
)
```

Prompts cannot override strong training priors consistently. **`ToolConfig` with `ANY` is the engineering fix** — it removes Gemini's choice to refuse.

### Fix checklist (in order)

| # | Check | Fix |
|---|-------|-----|
| 1 | Turn 1 uses `FunctionCallingConfigMode.ANY` | Add `enforcer_config` to `GenerateContentConfig` |
| 2 | `allowed_function_names` includes `get_employee_manager` | Restrict when multiple tools exist |
| 3 | `GET_MANAGER_TOOL` has `email` in `properties` and `required` | Match server function signature |
| 4 | Parameter description mentions email explicitly | `"The email address of the employee (e.g., ...)"` |
| 5 | `response.function_calls` checked before `call_tool` | Log `response.text` when empty |
| 6 | Turn 2 uses `AUTO` | Allows final natural-language summary |
| 7 | Server tool uses stderr for debug | Protect stdout JSON-RPC stream |

### Before vs after

**Before (`AUTO` only):**

```
User prompt → Gemini → "I need an employee ID" (text)
                     → MCP Server never contacted
                     → Task "succeeds" with useless answer
```

**After (`ANY` on Turn 1):**

```
User prompt → Gemini → function_call: get_employee_manager(email="katrina@newhire.com")
          → Client → Server → PostgreSQL
          → Gemini Turn 2 → "Contact Alice at alice@company.com for expense approval."
```

### Verify the Server was actually called

Add temporary stderr logging on the server (safe for stdio):

```python
import sys

@mcp.tool()
def get_employee_manager(email: str) -> str:
    print(f"[MCP SERVER] lookup for {email}", file=sys.stderr)
    ...
```

If Celery logs show Gemini text but stderr never prints `[MCP SERVER]`, the failure is **before** `call_tool` — fix Turn 1 / `ToolConfig`, not the database.

---

## 9. Step 3: Trigger via RabbitMQ

From Django shell, a view, or management command:

```python
from apps.organizations.tasks import run_ai_org_lookup

run_ai_org_lookup.delay("shuaib@workstack.dev")
```

Ensure `celery` and `rabbitmq` services are running (`make up`).

### What you will observe in Celery logs

| Phase | What happens |
|-------|----------------|
| **Task received** | Worker picks up message from RabbitMQ |
| **Subprocess spawned** | `mcp_org_server.py` starts; own Django + DB pool |
| **Turn 1** | Gemini returns structured `get_employee_manager` call |
| **tools/call** | JSON-RPC over stdin/stdout; ORM query runs |
| **Turn 2** | Result sent to Gemini; final paragraph logged |

Example log sequence:

```
[INFO] Task apps.organizations.tasks.run_ai_org_lookup[...] received
[INFO] Starting email task...   # (if chained with invite flow)
... Gemini function call ...
... Manager: Alice Example (alice@workstack.dev) ...
final_response >>> Contact Alice for expense approval ...
```

---

## 10. Step 4: SSE Daemon (Production-Style)

**File:** `backend/mcp_daemons/hr_server.py`

```python
import os
import sys
import django
from mcp.server.fastmcp import FastMCP

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local")
django.setup()  # ONCE at boot

from apps.hris.models import Employee

mcp = FastMCP("Workstack_HR_Daemon")

@mcp.tool()
def get_employee_manager(email: str) -> str:
    ...

if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8080)
```

### Local testing

```bash
# Option A: Docker
docker compose up mcp_hr_daemon

# Option B: Direct (from backend/)
python mcp_daemons/hr_server.py
```

Verify: `http://localhost:8080` (SSE endpoint per FastMCP).

### Celery as HTTP client (future wiring)

Today, `run_mcp_agent_loop` uses **stdio**. To use the SSE daemon:

1. Replace `stdio_client` with an SSE transport client pointing at `http://mcp_hr_daemon:8080` (Docker network) or `http://localhost:8080` (host).
2. Remove subprocess spawn — worker opens a persistent HTTP connection.
3. No per-task `django.setup()` — the daemon already holds a warm pool.

> **Status:** SSE server runs in Docker; Celery task still uses stdio path. Switching the client transport is the remaining integration step.

---

## 11. stdio vs SSE: Decision Matrix

| Concern | stdio subprocess | SSE daemon |
|---------|------------------|------------|
| Django boot | Every task (~1–2s) | Once at daemon start |
| DB connections | New pool per spawn | Shared warm pool |
| Deployment | No extra service | Separate container/service |
| Isolation | Strong (crash in child) | Shared process |
| Local dev | Simple (no port) | Run second terminal / compose service |
| Cursor / Claude Desktop | Native stdio config | Remote URL |

**Recommendation:**

| Environment | Use |
|-------------|-----|
| Local experimentation | stdio (`mcp_org_server.py`) |
| Docker compose / staging | SSE (`mcp_hr_daemon`) |
| Production | SSE + horizontal scaling behind load balancer |

Running `django.setup()` inside a stdio subprocess for **every** Celery task is an anti-pattern at scale. Use stdio to **prove the loop**; use SSE for **real traffic**.

---

## 12. Docker Production Setup

`docker-compose.yml` already defines the SSE service:

```yaml
mcp_hr_daemon:
  build:
    context: ./backend
  container_name: workstack_mcp_hr
  command: python mcp_daemons/hr_server.py
  volumes:
    - ./backend:/app
  ports:
    - "8080:8080"
  depends_on:
    - db
```

Treat `mcp_hr_daemon` like `celery`:

- Same backend image
- Own command
- Depends on `db` for PostgreSQL
- Add `env_file: .env` when you wire Gemini or DB URLs explicitly

**Production checklist:**

- [ ] Set `DJANGO_SETTINGS_MODULE=core.settings.production` on daemon
- [ ] Point Celery MCP client to `http://mcp_hr_daemon:8080`
- [ ] Add health checks / restart policy
- [ ] Restrict port 8080 to internal network only (not public internet)
- [ ] Remove debug `print()` from server tools; use structlog → stderr

---

## 13. Next Steps (Not Yet Implemented)

| Item | Description |
|------|-------------|
| Celery → SSE client | Replace stdio spawn with HTTP client to `mcp_hr_daemon` |
| SSE ToolConfig parity | Add `ANY`/`AUTO` enforcer to `run_mcp_agent_loop_sse` |
| `trace.md` | OS-level tracing (`ps`, `lsof`, pipe I/O) — planned separately |
| Auth on MCP | API key or mTLS between Celery and MCP daemon |
| Additional tools | Payroll, PTO balance, department listing |
| Shared tool module | DRY tool implementations imported by both stdio and SSE entrypoints |

---

## Quick Reference

| Action | Command / Code |
|--------|----------------|
| Start full stack | `make up` |
| Start MCP SSE only | `docker compose up mcp_hr_daemon` |
| Queue AI lookup | `run_ai_org_lookup.delay("user@company.com")` |
| stdio server path | `apps/organizations/management/commands/mcp_org_server.py` |
| SSE server path | `mcp_daemons/hr_server.py` |
| MCP port | `8080` |

---

[← MCP Deep Dive](MCP_DEEP_DIVE.md) · [← Main README](../README.md)
