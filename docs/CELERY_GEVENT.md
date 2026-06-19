# Celery with Gevent — High-Concurrency Workers

This guide explains when and how to run Celery workers with the **gevent** execution pool for I/O-bound tasks (LLM API calls, HTTP MCP clients, email webhooks) without spawning one OS process per concurrent task.

For web server concurrency (Gunicorn + Gevent vs Uvicorn), see [WSGI_GEVENT_VS_ASGI.md](WSGI_GEVENT_VS_ASGI.md).

---

## Table of Contents

1. [Default Celery vs Gevent Celery](#1-default-celery-vs-gevent-celery)
2. [When Gevent Helps (and When It Does Not)](#2-when-gevent-helps-and-when-it-does-not)
3. [Workstack Docker Configuration](#3-workstack-docker-configuration)
4. [Writing Async-Friendly Tasks](#4-writing-async-friendly-tasks)
5. [MCP + Gemini Tasks on Gevent](#5-mcp--gemini-tasks-on-gevent)
6. [Limitations and Pitfalls](#6-limitations-and-pitfalls)
7. [Monitoring and Tuning](#7-monitoring-and-tuning)

---

## 1. Default Celery vs Gevent Celery

### Default: prefork pool (what Workstack uses today)

```yaml
# docker-compose.yml — current
celery:
  command: celery -A core worker -l info
```

| Property | Behavior |
|----------|----------|
| Pool | `prefork` (default) |
| Concurrency | One OS process per worker slot (≈ CPU cores) |
| Blocking | Each process **fully blocks** until task finishes |
| Gemini call (10s) | That worker is frozen for 10 seconds |
| 9th simultaneous task | Waits in RabbitMQ queue |

With the default **prefork** pool, each worker process handles **one task at a time** until that task completes. High concurrency requires switching to a cooperative pool (for example **gevent**) **and** writing tasks that yield during I/O waits rather than blocking the worker for the full duration of external API calls.

### Gevent pool: cooperative greenlets

```yaml
# docker-compose.yml — future high-concurrency option (commented in repo)
celery:
  command: celery -A core worker -l info --pool=gevent --concurrency=1000
```

| Property | Behavior |
|----------|----------|
| Pool | `gevent` |
| Concurrency | Up to N **greenlets** per OS process (not N processes) |
| Blocking I/O | Gevent monkey-patches sockets; waiting on network **yields** |
| Many Gemini calls | One process can park 100s of waiting HTTP requests |

### How gevent changes the game

```
Prefork (8 workers, 8 tasks calling Gemini):
  Worker-1 ████████████ waiting 10s (blocked)
  Worker-2 ████████████ waiting 10s (blocked)
  ...
  Worker-8 ████████████ waiting 10s (blocked)
  Task 9   ⏳ queued in RabbitMQ

Gevent (1 process, concurrency=1000):
  Greenlet-1  → await Gemini → parked
  Greenlet-2  → await Gemini → parked
  ...
  Greenlet-50 → MCP HTTP call → parked
  (same OS process serves all while I/O waits)
```

---

## 2. When Gevent Helps (and When It Does Not)

### Good fit (I/O-bound)

- `run_ai_org_lookup` / `run_ai_org_lookup_sse` (Gemini HTTP + MCP HTTP)
- `send_magic_link_email` (future real SMTP/SES API)
- External webhook delivery
- Scraping, file uploads to S3

### Poor fit (CPU-bound)

- Large PDF generation
- Image processing
- Heavy pandas/numpy in Celery

CPU-bound work still holds the GIL; gevent does not parallelize CPU across greenlets.

### Requires gevent-compatible code

Plain synchronous `requests.get()` works **after** monkey patching.  
`asyncio.run()` inside every task fights gevent's model — prefer one style:

| Approach | Celery pool | Task style |
|----------|-------------|------------|
| Prefork + asyncio | `prefork` (default) | `asyncio.run(run_mcp_agent_loop(...))` ✅ current |
| Gevent + sync I/O | `gevent` | Sync HTTP clients, no nested event loops |
| Gevent + async | `gevent` + special setup | Advanced; not default |

Workstack's current tasks use `asyncio.run()` — they work on **prefork**. Before switching to gevent, refactor MCP tasks to sync HTTP or use a gevent-native async bridge.

---

## 3. Workstack Docker Configuration

### Install gevent

Add to `backend/requirements/production.txt` (or local for testing):

```
gevent>=24.0
```

### Docker Compose command

```yaml
celery:
  build:
    context: ./backend
  container_name: workstack_celery
  # Default (current) — one task blocks one worker process:
  command: celery -A core worker -l info

  # High-concurrency I/O-bound tasks (future):
  # command: celery -A core worker -l info --pool=gevent --concurrency=500
  env_file:
    - .env
  depends_on:
    - db
    - redis
    - rabbitmq
```

### Monkey patching for Celery gevent

Celery's gevent pool applies patches when `--pool=gevent` is used. If you import blocking libraries before Celery boots, patch order matters. Keep task modules free of early socket use before worker init.

Optional explicit patch in `core/celery.py` (only if needed):

```python
# core/celery.py — only when using gevent pool
from gevent import monkey
monkey.patch_all()
```

---

## 4. Writing Async-Friendly Tasks

### Current Workstack pattern (prefork + asyncio)

```python
@shared_task
def run_ai_org_lookup(target_email):
    return asyncio.run(run_mcp_agent_loop(target_email))
```

Works with default prefork. Each task gets its own process and its own event loop — simple and isolated.

### Gevent-friendly pattern (future)

Prefer synchronous I/O in tasks when on gevent pool, or use `gevent`-compatible HTTP libraries after monkey patch:

```python
@shared_task
def send_webhook(url, payload):
    import requests
    requests.post(url, json=payload, timeout=30)  # yields under gevent
```

Avoid nesting `asyncio.run()` heavily inside gevent workers unless you know the interaction.

---

## 5. MCP + Gemini Tasks on Gevent

Production pipeline:

```
RabbitMQ → Celery (gevent) → Gemini HTTPS (slow)
                          → MCP SSE http://workstack_mcp_hr:8080/sse (fast)
```

| Stage | Latency | Gevent benefit |
|-------|---------|----------------|
| Gemini Turn 1 | 2–10s | Worker parks greenlet, picks other tasks |
| MCP `call_tool` | ~50ms | Short HTTP; still benefits under load |
| Gemini Turn 2 | 2–10s | Same parking behavior |

The **MCP SSE daemon** is separate from Celery concurrency — it has its own ASGI event loop. Gevent scales **how many Celery tasks** can wait on Gemini simultaneously.

---

## 6. Limitations and Pitfalls

| Pitfall | Detail |
|---------|--------|
| **Not true parallelism for CPU** | GIL + greenlets = one Python bytecode runner |
| **Mixing asyncio.run + gevent** | Can deadlock or serialize unexpectedly |
| **Non-patched C extensions** | Some drivers ignore gevent; test thoroughly |
| **Database connections** | Use Django CONN_MAX_AGE carefully; pool per process still applies |
| **Debugging** | Stack traces across greenlets are harder than prefork |

### psycopg3 note

Workstack uses `psycopg[binary]` (v3). It integrates with gevent when `monkey.patch_all()` runs — **psycogreen is only needed for psycopg2**.

---

## 7. Monitoring and Tuning

```bash
# Watch worker pool
docker compose exec celery celery -A core inspect active

# RabbitMQ queue depth
# http://localhost:15672 (management UI)
```

Start conservative:

```bash
celery -A core worker --pool=gevent --concurrency=100 -l info
```

Increase `--concurrency` while watching PostgreSQL connection count and memory.

---

## Decision Summary

| Scenario | Recommendation |
|----------|----------------|
| Dev / low traffic | Default prefork ✅ (current) |
| Many concurrent LLM tasks | `--pool=gevent --concurrency=500+` |
| Heavy CPU in tasks | Separate queue + prefork workers |
| MCP SSE daemon | Always separate container — not a Celery pool substitute |

---

[← WSGI vs ASGI Guide](WSGI_GEVENT_VS_ASGI.md) · [← MCP SSE Guide](MCP_SSE_HTTP.md) · [← README](../README.md)
