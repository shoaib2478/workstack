# Workstack

Workstack is a multi-tenant **HRIS (Human Resource Information System)** backend — a modern platform for managing organizations, employees, org charts, roles, and permissions. It is built with Django, Django REST Framework, and production-grade infrastructure (PostgreSQL, Redis, RabbitMQ, Nginx, Docker).

This repository is the API layer. A React frontend (not included here) is expected to consume the REST API at `http://localhost:8000/api/v1/`.

---

## Table of Contents

- [What Workstack Covers](#what-workstack-covers)
- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Makefile Commands](#makefile-commands)
- [Environment & Settings](#environment--settings)
- [Authentication (HttpOnly JWT Cookies)](#authentication-httponly-jwt-cookies)
- [Org Chart (django-treebeard)](#org-chart-django-treebeard)
- [RBAC & ReBAC](#rbac--rebac)
- [User Invite Flow](#user-invite-flow)
- [Background Jobs (Celery)](#background-jobs-celery)
- [Nginx & Static Files](#nginx--static-files)
- [MCP Integration (AI Tools)](#mcp-integration-ai-tools)
- [AI Agent Workflows (Phase 5)](#ai-agent-workflows-phase-5)
- [Documentation Index](#documentation-index)

---

## What Workstack Covers

| Area | Implementation |
|------|----------------|
| **Multi-tenant SaaS** | Organizations, memberships, per-org settings |
| **Secure auth** | SimpleJWT stored in **HttpOnly cookies** (not `localStorage`) to reduce XSS token theft |
| **Org chart** | **django-treebeard** materialized-path trees — subtree queries without recursive SQL joins |
| **Authorization** | **RBAC** (role → permission codes, Redis cache-aside) + **ReBAC** (manager hierarchy via tree paths) |
| **Async work** | **Celery** workers backed by **RabbitMQ**; results in **Redis** |
| **Production Docker** | Gunicorn, Nginx reverse proxy, shared static volume, health-aware entrypoint |
| **Split settings** | `core.settings.local` vs `core.settings.production` |
| **End-to-end invites** | Signed invite tokens (`TimestampSigner`), magic-link email task, org-chart placement on accept |
| **MCP / AI tools** | Gemini + MCP loop (stdio for dev; SSE daemon for production-style serving) |
| **AI agents** | Celery Canvas + LangGraph + LangChain MCP adapters (`apps/incidents/`) |

---

## Architecture Overview

```
                    ┌─────────────┐
                    │   Nginx :80 │
                    └──────┬──────┘
                           │ proxy
                    ┌──────▼──────┐     ┌──────────┐
                    │  Gunicorn   │────►│ Postgres │
                    │  (web) :8000│     └──────────┘
                    └──────┬──────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
  ┌──────▼──────┐   ┌──────▼──────┐   ┌──────▼──────┐
  │   Celery    │   │    Redis    │   │  RabbitMQ   │
  │   worker    │   │ cache/results│   │   broker    │
  └──────┬──────┘   └─────────────┘   └─────────────┘
         │
         │  chord → LangGraph agent → MCP tools
         ▼
  ┌──────────────────┐
  │ mcp_hr_daemon    │  MCP SSE :8080 (hr_server.py)
  └──────────────────┘
```

**Services** (see `docker-compose.yml`):

| Service | Port | Role |
|---------|------|------|
| `web` | 8000 | Django API (Gunicorn) |
| `nginx` | 80 | Reverse proxy, static files |
| `db` | 5432 | PostgreSQL 15 |
| `redis` | 6379 | Cache + Celery result backend |
| `rabbitmq` | 5672 / 15672 | Message broker + management UI |
| `celery` | — | Async task worker |
| `mcp_hr_daemon` | 8080 | Persistent MCP SSE server |

---

## Project Structure

```
workstack_project/
├── backend/
│   ├── apps/
│   │   ├── users/           # Auth, signup, JWT cookie login
│   │   ├── organizations/   # Phase 4: invites, MCP stdio tasks (unchanged)
│   │   ├── incidents/       # Phase 5: Celery Canvas + LangGraph agents
│   │   ├── rbac/            # Roles, permissions, RBACService
│   │   └── hris/            # Employee org chart, ReBAC permissions
│   ├── core/
│   │   ├── settings/        # base.py, local.py, production.py
│   │   ├── celery.py
│   │   └── permissions.py   # HasOrganizationPermission
│   ├── mcp_daemons/         # Persistent SSE MCP servers (production-style)
│   │   └── hr_server.py
│   ├── scripts/entrypoint.sh
│   ├── manage.py
│   └── Dockerfile
├── docs/                    # Deep-dive documentation
├── nginx/default.conf
├── docker-compose.yml
├── Makefile
└── .env                     # Local secrets (not committed)
```

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- A `.env` file at the project root (see [Environment & Settings](#environment--settings))

### Run the stack

```bash
make build
make up
```

API: `http://localhost:8000/api/v1/`  
Through Nginx: `http://localhost/api/v1/`  
RabbitMQ UI: `http://localhost:15672`

Apply migrations manually if needed:

```bash
make migrate
```

---

## Makefile Commands

| Command | Description |
|---------|-------------|
| `make help` | Show available targets |
| `make build` | Build Docker images |
| `make up` | Start all services (detached) |
| `make down` | Stop containers (keeps volumes) |
| `make clean` | Stop containers **and wipe volumes** |
| `make logs` | Tail logs for all services |
| `make dev` | Start `web` with build + attached logs |
| `make shell` | Django shell inside `web` container |
| `make backendshell` | Bash shell inside `web` container |
| `make dbshell` | PostgreSQL shell |
| `make migrate` | Run migrations |
| `make makemigrations` | Create new migrations |

---

## Environment & Settings

Settings are split for safe local development vs production hardening.

| Module | Purpose |
|--------|---------|
| `core.settings.base` | Shared config: apps, DRF, JWT, DB, Redis, Celery, structlog |
| `core.settings.local` | `DEBUG=True`, permissive hosts, dev secret key default |
| `core.settings.production` | `DEBUG=False`, HSTS, secure cookies, SSL redirect |

Set `DJANGO_SETTINGS_MODULE` (default in Docker/local: `core.settings.local`).

Example `.env` variables:

```env
DATABASE_URL=postgres://workstackuser:workstack@db:5432/workstack
REDIS_URL=redis://redis:6379/1
CELERY_BROKER_URL=amqp://workstackuser:workstack@rabbitmq:5672//
CELERY_RESULT_BACKEND=redis://redis:6379/2
DJANGO_SECRET_KEY=your-secret-key
GEMINI_API_KEY=your-gemini-key   # Required for MCP AI tasks
```

Production additionally requires `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and `AUTH_COOKIE_SECURE=True` (via production settings).

---

## Authentication (HttpOnly JWT Cookies)

Workstack uses **djangorestframework-simplejwt** but deliberately avoids storing tokens in browser `localStorage`, which is readable by any XSS script.

**Flow:**

1. Login (`POST /api/v1/auth/login/`) — tokens are generated, then **removed from the JSON body**.
2. `set_jwt_cookies()` writes `access_token` and `refresh_token` as **HttpOnly**, **SameSite=Lax** cookies.
3. `CustomCookieJWTAuthentication` reads the access token from cookies on each request (Bearer header still works for Postman/cURL).

Relevant files:

- `backend/apps/users/authentication.py` — cookie-based JWT extraction
- `backend/core/utils/auth.py` — `set_jwt_cookies()`
- `backend/core/settings/base.py` — `SIMPLE_JWT` and `CORS_ALLOW_CREDENTIALS`

---

## Org Chart (django-treebeard)

Employees inherit from Treebeard's `MP_Node` (materialized path). Each node stores a `path` string (e.g. `000100020005`) instead of relying on deep recursive joins.

**Why it matters:**

- Query all reports under a VP: `path LIKE '00010002%'` — indexed, milliseconds at scale.
- Move a subtree: Treebeard updates paths for the employee and all descendants atomically.

**Service layer:** `apps/hris/service/org_chart.py` — `add_employee`, `move_employee`, `get_reporting_chain`, `get_all_descendants`.

**Model:** `apps/hris/models.py` — composite indexes on `(organization, path)` for multi-tenant isolation.

---

## RBAC & ReBAC

### RBAC (Role-Based Access Control)

- Permissions are global codes (`users:read`, `users:write`, `org:write`, …).
- Roles are **per-organization**; members receive roles via `MemberRole`.
- `RBACService` uses a **cache-aside pattern** (Redis, 1-hour TTL) for permission lookups.
- DRF views use `HasOrganizationPermission('users:write')` with the `X-Organization-Id` header.

Files: `apps/rbac/services.py`, `core/permissions.py`.

### ReBAC (Relationship-Based Access Control)

`IsManagerOfEmployee` in `apps/hris/permissions.py` grants object-level access when the requesting user is an **ancestor** in the org tree (or the employee themselves). Treebeard's `is_descendant_of()` compares path prefixes in O(1) — no recursive SQL.

---

## User Invite Flow

End-to-end flow from admin invite to org-chart placement:

```
Admin POST /invite/  →  InviteUserService  →  TimestampSigner token
                              ↓
                    Celery: send_magic_link_email (15s delay)
                              ↓
User clicks link  →  POST /accept-invite/  →  unsign token (48h max_age)
                              ↓
              set password, activate membership, OrgChartService.add_employee
                              ↓
                    HttpOnly JWT cookies (auto login)
```

**Token payload** (signed, tamper-evident):

```python
{
    "user_id": "...",
    "organization_id": "...",
    "membership_id": "...",
    "inviter_id": "...",
    "manager_id": "..."   # optional; defaults to inviter as manager
}
```

**Key files:**

- `apps/organizations/service/invites.py` — invite creation + async email
- `apps/organizations/views.py` — `InviteUserView`, `AcceptInviteView`
- `apps/organizations/tasks.py` — `send_magic_link_email`

Invited users start with `is_active=False` on their membership until they accept.

---

## Background Jobs (Celery)

- **Broker:** RabbitMQ (`CELERY_BROKER_URL`)
- **Results:** Redis (`CELERY_RESULT_BACKEND`)
- **Worker:** `celery -A core worker` (see `docker-compose.yml` service `celery`)

Tasks live in app modules (e.g. `apps.organizations.tasks`). The entrypoint runs migrations only for `web`, not for Celery workers, avoiding race conditions.

**Trigger an AI org lookup** (requires `GEMINI_API_KEY`):

```python
from apps.organizations.tasks import run_ai_org_lookup
run_ai_org_lookup.delay("employee@workstack.dev")
```

Watch Celery logs for the full Gemini ↔ MCP ↔ PostgreSQL handshake.

---

## Nginx & Static Files

`nginx/default.conf`:

- Proxies `/` to Gunicorn (`web:8000`)
- Serves `/static/` from a shared Docker volume (`static_volume`) populated by `collectstatic`

This keeps static assets off the Python worker process and allows long cache headers in production.

---

## MCP Integration (AI Tools)

Workstack integrates the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) so LLMs (Gemini) can call HR tools backed by real Django models and PostgreSQL data.

Two transport modes are implemented:

| Mode | Location | When to use |
|------|----------|-------------|
| **stdio** | `apps/organizations/management/commands/mcp_org_server.py` | Local dev, Celery subprocess spawning (1–2s Django boot per call) |
| **SSE/HTTP** | `backend/mcp_daemons/hr_server.py` | Persistent daemon, Docker service `mcp_hr_daemon` on port **8080** |

The Celery tasks `run_ai_org_lookup` (stdio) and `run_ai_org_lookup_sse` (HTTP) orchestrate: Gemini → MCP → PostgreSQL → Gemini.

> **Status:** SSE daemon tested via `apps.organizations.tests.test_mcp_sse`. See [docs/MCP_SSE_HTTP.md](docs/MCP_SSE_HTTP.md).

For the full agent stack (LangGraph + Celery Canvas), see [AI Agent Workflows](#ai-agent-workflows-phase-5).

---

## AI Agent Workflows (Phase 5)

Production-style **AI agent** pattern: separate deterministic I/O from LLM reasoning.

| Layer | Technology | Location |
|-------|------------|----------|
| **Muscle** | Celery `group` + `chord` | `apps/incidents/tasks.py` |
| **Brain** | LangGraph `StateGraph` | Same file |
| **Toolkit** | LangChain + Gemini | `ChatGoogleGenerativeAI`, `ToolNode` |
| **Tools** | MCP via `MultiServerMCPClient` | Connects to `mcp_daemons/hr_server.py` |

**Scenario:** Automated Incident Triage — parallel fetch from Datadog/GitHub/Slack, then LangGraph agent looks up commit author's manager via MCP and drafts an incident report.

```python
from apps.incidents.tasks import trigger_incident_workflow
trigger_incident_workflow()
```

Phase 4 code in `apps/organizations/tasks.py` is **unchanged** — it remains the minimal MCP + Gemini reference.

> **Architecture verdict:** Separate `incidents` app + shared `mcp_daemons/` is the recommended layout. See [docs/AGENT_ARCHITECTURE.md](docs/AGENT_ARCHITECTURE.md).

---

## Documentation Index

### MCP (Phase 4)

| Document | Description |
|----------|-------------|
| [docs/MCP_DEEP_DIVE.md](docs/MCP_DEEP_DIVE.md) | MCP concepts, transports (stdio vs SSE), FAQ, and protocol flow |
| [docs/MCP_INTEGRATION.md](docs/MCP_INTEGRATION.md) | stdio integration: Host→Client→Server flow, `ToolConfig`, Gemini errors |
| [docs/MCP_SSE_HTTP.md](docs/MCP_SSE_HTTP.md) | SSE/HTTP daemon: `hr_server.py`, `sync_to_async`, isolated testing |
| [docs/TRIAGE_PRODUCT.md](docs/TRIAGE_PRODUCT.md) | Triage product — chunking, live SSE UI, configurable integrations roadmap |
| [docs/MCP_PROTOCOL_GAPS_AND_CONTRIBUTIONS.md](docs/MCP_PROTOCOL_GAPS_AND_CONTRIBUTIONS.md) | MCP production gaps (transport, payloads, scale) + fix status |

### AI Agents (Phase 5)

| Document | Description |
|----------|-------------|
| [docs/AGENT_ARCHITECTURE.md](docs/AGENT_ARCHITECTURE.md) | Repo layout, `incidents` app, MCP vs LangGraph vs Celery |
| [docs/LANGGRAPH_DEEP_DIVE.md](docs/LANGGRAPH_DEEP_DIVE.md) | LangGraph nodes, ReAct vs state machine, who decides |
| [docs/LANGCHAIN_MCP_INTEGRATION.md](docs/LANGCHAIN_MCP_INTEGRATION.md) | `MultiServerMCPClient`, multi-server tools, Phase 4 vs 5 |
| [docs/INCIDENT_TRIAGE_AGENT.md](docs/INCIDENT_TRIAGE_AGENT.md) | Automated Incident Triage — **22s log autopsy**, code map, tests |
| [docs/INCIDENT_TRIAGE_LOCAL_TEST.md](docs/INCIDENT_TRIAGE_LOCAL_TEST.md) | **Local test walkthrough** — Phase 2 chunking output, Redis `:1:` prefix, get_chunk caveat |
| [docs/INCIDENT_TRIAGE_QA.md](docs/INCIDENT_TRIAGE_QA.md) | Triage Q&A — chunking, Redis, run_id vs ref_id, LangGraph choices |
| [docs/INCIDENT_TRIAGE_RESEARCH.md](docs/INCIDENT_TRIAGE_RESEARCH.md) | Product research — Pattern A/B/C, vector ingest roadmap, chunking as library |

### Infrastructure

| Document | Description |
|----------|-------------|
| [docs/CELERY_GEVENT.md](docs/CELERY_GEVENT.md) | Celery `--pool=gevent` for concurrent LLM/MCP I/O tasks |
| [docs/WSGI_GEVENT_VS_ASGI.md](docs/WSGI_GEVENT_VS_ASGI.md) | WSGI+Gevent vs Uvicorn+ASGI vs DRF thread pool — scaling guide |

### MCP SSE quick test

```bash
docker compose up mcp_hr_daemon -d
docker compose exec web python manage.py test apps.organizations.tests.test_mcp_sse -v 2
```

### Incident agent quick test

```bash
docker compose exec web python manage.py shell
>>> from apps.incidents.tasks import trigger_incident_workflow
>>> trigger_incident_workflow()
# Watch: docker compose logs celery -f
```

---

## API Overview

| Prefix | App |
|--------|-----|
| `/api/v1/` | Users, auth, signup |
| `/api/v1/rbac/` | Roles & permissions |
| `/api/v1/organizations/` | Orgs, invites |
| `/api/v1/hris/` | Employees, org chart |

---

## License

Private / internal project — add license terms as needed.
