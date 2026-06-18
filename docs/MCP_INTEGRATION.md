# MCP Integration Guide — Workstack

This guide covers **where MCP servers live in the repo**, how **Django initialization** works in subprocess vs daemon mode, the **Celery + Gemini loop**, and how to run **stdio (dev)** vs **SSE/HTTP (production-style)** locally.

For protocol concepts and FAQ, see [MCP_DEEP_DIVE.md](MCP_DEEP_DIVE.md).

---

## Table of Contents

1. [The Django Initialization Problem](#1-the-django-initialization-problem)
2. [Where Should MCP Servers Live?](#2-where-should-mcp-servers-live)
3. [Step 1: stdio MCP Server (Development)](#3-step-1-stdio-mcp-server-development)
4. [Step 2: Celery Task with Gemini Loop](#4-step-2-celery-task-with-gemini-loop)
5. [Step 3: Trigger via RabbitMQ](#5-step-3-trigger-via-rabbitmq)
6. [Step 4: SSE Daemon (Production-Style)](#6-step-4-sse-daemon-production-style)
7. [stdio vs SSE: Decision Matrix](#7-stdio-vs-sse-decision-matrix)
8. [Docker Production Setup](#8-docker-production-setup)
9. [Next Steps (Not Yet Implemented)](#9-next-steps-not-yet-implemented)

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

## 4. Step 2: Celery Task with Gemini Loop

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

---

## 5. Step 3: Trigger via RabbitMQ

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

## 6. Step 4: SSE Daemon (Production-Style)

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

## 7. stdio vs SSE: Decision Matrix

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

## 8. Docker Production Setup

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

## 9. Next Steps (Not Yet Implemented)

| Item | Description |
|------|-------------|
| Celery → SSE client | Replace stdio spawn with HTTP client to `mcp_hr_daemon` |
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
