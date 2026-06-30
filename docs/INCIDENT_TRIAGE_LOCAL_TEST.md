# Incident Triage — Local Test Walkthrough

Copy-paste shell session for **Phase 2–5** (chunking, full triage, checkpoints, live stream) with expected output and verified test results. Run inside Docker.

**Full runbook:** [INCIDENT_TRIAGE_AGENT.md](INCIDENT_TRIAGE_AGENT.md) §6 · **Q&A:** [INCIDENT_TRIAGE_QA.md](INCIDENT_TRIAGE_QA.md)

---

## Setup

```bash
cd ~/django_projects/workstack_project

# Full stack for Phase 3
docker compose up -d db redis rabbitmq web celery mcp_hr_daemon

# After .env changes (e.g. TRIAGE_MAX_INLINE_CHARS)
docker compose up -d --force-recreate celery
```

**Terminal layout for Phase 3:**

| Terminal | Command |
|----------|---------|
| **A** | `docker compose logs celery -f` |
| **B** | `docker compose exec web python manage.py shell` |
| **C** | `curl` for Phase 4 / 5 checkpoints (optional) |

---

## Phase 2 — Chunking (two tests)

### Flow overview

```mermaid
flowchart TB
    subgraph TestA["Test A — mock fetchers (no chunking)"]
        A1[fetch_datadog / github / slack] --> A2[build_chunked_log_context]
        A2 --> A3{each payload &lt; 8000 chars?}
        A3 -->|Yes| A4[Full JSON inline in prompt]
        A3 -->|No| A5[Would TRIAGE_REF — not hit with mocks]
    end

    subgraph TestB["Test B — forced chunk (ERROR × 50)"]
        B1[big dict → JSON 350 chars] --> B2{len &gt; 80 inline limit?}
        B2 -->|Yes| B3[Redis: full 350 chars]
        B2 -->|Yes| B4[Prompt: first 80 chars + TRIAGE_REF]
        B3 --> B5[get_chunk ref, 0 → 40 chars<br/>get_chunk ref, 1 → next 40 …]
    end
```

| Test | Function | Chunking? | Proves |
|------|----------|-----------|--------|
| **A** | `build_chunked_log_context(logs, run_id)` | No — mocks are small | Normal path: full telemetry inline, no `[TRIAGE_REF]` |
| **B** | `prepare_payload_for_prompt(...)` inside `override_settings` | Yes — artificial 80-char cap | Overflow → Redis + marker + slice reads |

---

### Test A — mock logs (no chunking)

```python
from apps.incidents.tasks import (
    fetch_datadog_metrics,
    fetch_github_commits,
    fetch_slack_alerts,
)
from apps.incidents.chunking import build_chunked_log_context

run_id = "shell-chunk-test"
logs = [
    fetch_datadog_metrics("srv-production-01"),
    fetch_github_commits("srv-production-01"),
    fetch_slack_alerts("srv-production-01"),
]

print(build_chunked_log_context(logs, run_id)[0][:400])
```

**Expected output (abbreviated):**

```text
### Datadog
{
  "source": "Datadog",
  "cpu_usage": "99%",
  "status": "critical"
}

### GitHub
{
  "source": "GitHub",
  "recent_commit": "Update nginx config",
  "author": "katrina@newhire.com"
}

### Slack
...
```

**Pass criteria:** Full JSON for each source. **No** `[TRIAGE_REF` anywhere. Same behavior as real triage when logs fit in `TRIAGE_MAX_INLINE_CHARS` (default 8000).

**With `TRIAGE_MAX_INLINE_CHARS=50` in `.env`:** mock fetchers **will** truncate (each JSON &gt; 50 chars). Celery logs show:

```text
--- CHUNKING SUMMARY (testing — comment out in tasks.py when done) ---
TRIAGE_MAX_INLINE_CHARS=50 TRIAGE_CHUNK_SIZE=4000
  [Datadog] TRUNCATED → Redis | total=... inline=... chunks=1
    reference_id=<run_id>:Datadog:...
    marker: ...[TRIAGE_REF source=Datadog id=...
--- END CHUNKING SUMMARY ---
```

Restart Celery after changing `.env`: `docker compose up -d --force-recreate celery`

---

### Test B — forced chunking

```python
from apps.incidents.chunking import prepare_payload_for_prompt, get_chunk
from django.test.utils import override_settings

run_id = "shell-chunk-test"

with override_settings(TRIAGE_MAX_INLINE_CHARS=80, TRIAGE_CHUNK_SIZE=40):
    big = {"source": "Datadog", "lines": ["ERROR " * 50]}
    r = prepare_payload_for_prompt("Datadog", big, run_id)

    print("truncated:", r.truncated)
    print("reference_id:", r.reference_id)
    print("total_chars:", r.total_chars)
    print("chunk_count:", r.chunk_count)
    print("inline tail:", r.inline[-100:])
    print("chunk 0:", repr(get_chunk(r.reference_id, 0)))
    print("chunk 1:", repr(get_chunk(r.reference_id, 1)))
    print("chunk 2:", repr(get_chunk(r.reference_id, 2)))
```

**Expected output:**

```text
truncated: True
reference_id: shell-chunk-test:Datadog:40c13715
total_chars: 350
chunk_count: 9
inline tail: ...TRIAGE_REF source=Datadog id=shell-chunk-test:Datadog:40c13715 total_chars=350 chunks=9 inline_limit=80]
chunk 0: '{\n  "source": "Datadog",\n  "lines": [\n    "ER'
chunk 1: 'ROR ERROR ERROR ERROR ERROR ERROR ERROR ERROR ER'
chunk 2: 'ROR ERROR ERROR ERROR ERROR ERROR ERROR ERROR ER'
```

| Field | Value | Meaning |
|-------|-------|---------|
| `total_chars` | 350 | Full JSON size |
| `chunk_count` | 9 | `ceil(350 / 40)` |
| `inline` | First 80 chars + marker | What the LLM sees in turn 1 |
| `chunk 0` | 40 chars | Slice `[0:40]` of full JSON |

**Important:** Call `get_chunk()` **inside** the same `with override_settings(...)` block.  
`TRIAGE_CHUNK_SIZE` defaults to **4000** outside that block — then chunk 0 returns the **entire** 350-char string and chunk 1 shows out of range:

```text
# WRONG — ran get_chunk in a new shell cell without override_settings
chunk 0: '{ ... entire 350 char JSON ... }'
chunk 1: '[chunk 1 out of range for reference shell-chunk-test:Datadog:40c13715]'
```

That is **settings**, not a bug.

---

### Read full blob from Redis (pretty)

Use Django cache — **not** raw `redis-cli GET` (pickle binary).

```python
from django.core.cache import cache
from apps.incidents.chunking import get_chunk
from django.test.utils import override_settings

ref = "shell-chunk-test:Datadog:40c13715"  # paste your reference_id

full = cache.get(f"triage:ref:{ref}")
print("Full length:", len(full))  # 350

with override_settings(TRIAGE_CHUNK_SIZE=40):
    for i in range(3):
        print(f"chunk {i}:", repr(get_chunk(ref, i)))
```

**Expected:**

```text
Full length: 350
chunk 0: '{\n  "source": "Datadog",\n  "lines": [\n    "ER'
chunk 1: 'ROR ERROR ERROR ERROR ERROR ERROR ERROR ERROR ER'
chunk 2: 'ROR ERROR ERROR ERROR ERROR ERROR ERROR ERROR ER'
```

---

### Redis CLI — key exists only

```bash
docker compose exec redis redis-cli -n 1 KEYS "*triage:ref*"
```

**Expected:**

```text
1) ":1:triage:ref:shell-chunk-test:Datadog:40c13715"
```

| Symbol | Meaning |
|--------|---------|
| `:1:` | Django cache version prefix — **success**, not an error |
| Pickle blob on `GET` | Normal — use `cache.get()` in Python for readable JSON |

```bash
# Shows binary pickle — content is mostly "ERROR ERROR ..." from test data
docker compose exec redis redis-cli -n 1 GET ":1:triage:ref:shell-chunk-test:Datadog:40c13715"
```

---

## Phase 2 checklist

| Step | Pass? |
|------|-------|
| Test A: full inline JSON, no `TRIAGE_REF` | |
| Test B: `truncated=True`, `reference_id` set | |
| Test B: `chunk_count=9` for 350 chars / size 40 | |
| `KEYS *triage:ref*` returns `:1:triage:ref:...` | |
| `cache.get` → `len(full)==350` | |
| `get_chunk` slices inside `override_settings` | |

---

## Phase 3 — Full flow (Celery → chunk → LangGraph → MCP SSE → Gemini)

### Flow overview

```mermaid
sequenceDiagram
    participant Shell as Django shell
    participant Celery as Celery workers
    participant Chunk as chunking.py
    participant Redis as Redis
    participant MCP as mcp_hr_daemon SSE
    participant LG as LangGraph ReAct
    participant Gemini as Gemini 2.5 Flash

    Shell->>Celery: trigger_incident_workflow()
    par chord scatter ~1s
        Celery->>Celery: fetch_datadog / github / slack
    end
    Celery->>Chunk: build_chunked_log_context
    Chunk->>Redis: store refs if truncated
    Note over Celery: log_chunking_summary in logs
    Celery->>MCP: GET /sse + ListTools
    Celery->>LG: agent.ainvoke(prompt)
    LG->>Gemini: turn 1 — tool call?
    Gemini-->>LG: get_employee_manager
    LG->>MCP: CallToolRequest
    MCP-->>LG: manager from Postgres
    LG->>Gemini: turn 2 — final report
    Gemini-->>LG: incident report text
    Celery->>Celery: FINAL AI AGENT OUTPUT
```

### Trigger

**Terminal B:**

```bash
docker compose exec web python manage.py shell
```

```python
from apps.incidents.tasks import trigger_incident_workflow

result = trigger_incident_workflow()
run_id = result["run_id"]
print(run_id)
# Live stream URL printed: /api/v1/incidents/runs/<run_id>/stream/
```

**Terminal A** — watch Celery (~20–30s).

---

### Mode A — default (`TRIAGE_MAX_INLINE_CHARS=8000`)

Mock fetchers are small → **no truncation**, no Redis refs for this run.

**Chunking summary in Celery (all inline):**

```text
--- CHUNKING SUMMARY (testing — comment out in tasks.py when done) ---
TRIAGE_MAX_INLINE_CHARS=8000 TRIAGE_CHUNK_SIZE=4000
  [Datadog] inline (full) | total=71 inline=71 chunks=1
  [GitHub] inline (full) | total=101 inline=101 chunks=1
  [Slack] inline (full) | total=93 inline=93 chunks=1
--- END CHUNKING SUMMARY ---
```

**Typical Celery tail:**

```text
[INFO] Task ... fetch_* succeeded (×3)
[INFO] Task ... run_mcp_enhanced_triage received
[INFO] HTTP Request: GET http://workstack_mcp_hr:8080/sse "HTTP/1.1 200 OK"
[INFO] HTTP Request: POST .../gemini-2.5-flash:generateContent "HTTP/1.1 200 OK"
... CallToolRequest in mcp_hr_daemon logs ...
--- FINAL AI AGENT OUTPUT ---
Subject: EMERGENCY INCIDENT REPORT: Critical Issue on srv-production-01
To: shuaib@acmecorp.com
...
[INFO] Task ... run_mcp_enhanced_triage succeeded
```

| Check | Pass criteria |
|-------|---------------|
| Chord | 3 fetchers succeed, then triage callback |
| Chunking | All sources `inline (full)` — no `TRIAGE_REF` |
| MCP | SSE 200, `CallToolRequest` in `mcp_hr_daemon` logs |
| LLM | Gemini 200 (503 retries OK), final report text |
| HR tool | Manager email from DB (e.g. `shuaib@acmecorp.com`) |

---

### Mode B — forced truncate (`TRIAGE_MAX_INLINE_CHARS=50`)

Set in `.env`, then **recreate Celery**:

```env
TRIAGE_MAX_INLINE_CHARS=50
```

```bash
docker compose up -d --force-recreate celery
```

Re-run `trigger_incident_workflow()`. **Verified test run** (`run_id=2db76262-6b26-49b9-83ef-64384ca1cab0`):

**Chunking summary (Celery WARNING lines):**

```text
--- CHUNKING SUMMARY (testing — comment out in tasks.py when done) ---
TRIAGE_MAX_INLINE_CHARS=50 TRIAGE_CHUNK_SIZE=4000
  [Datadog] TRUNCATED → Redis | total=71 inline=176 chunks=1
    reference_id=2db76262-6b26-49b9-83ef-64384ca1cab0:Datadog:cd9918fa
    marker: ...[TRIAGE_REF source=Datadog id=2db76262-6b26-49b9-83ef-64384ca1cab0:Datadog:cd9918fa total_chars=71 chunks=1 inline_limit
  [GitHub] TRUNCATED → Redis | total=101 inline=175 chunks=1
    reference_id=2db76262-6b26-49b9-83ef-64384ca1cab0:GitHub:f396ea31
    marker: ...[TRIAGE_REF source=GitHub id=2db76262-6b26-49b9-83ef-64384ca1cab0:GitHub:f396ea31 total_chars=101 chunks=1 inline_limit=
  [Slack] TRUNCATED → Redis | total=93 inline=172 chunks=1
    reference_id=2db76262-6b26-49b9-83ef-64384ca1cab0:Slack:05233f54
    marker: ...[TRIAGE_REF source=Slack id=2db76262-6b26-49b9-83ef-64384ca1cab0:Slack:05233f54 total_chars=93 chunks=1 inline_limit=50]
--- END CHUNKING SUMMARY ---
```

| Field | Example | Meaning |
|-------|---------|---------|
| `total=71` | Datadog JSON size | Full payload stored in Redis |
| `inline=176` | Datadog | First 50 chars + `\n\n` + `[TRIAGE_REF ...]` marker |
| `chunks=1` | All sources | One slice — payload &lt; `TRIAGE_CHUNK_SIZE` (4000) |
| `reference_id` | `{run_id}:Datadog:cd9918fa` | Key for `cache.get("triage:ref:...")` |

**Why `inline` &gt; 50?** Inline = truncated JSON **plus** the `[TRIAGE_REF ...]` footer sent to Gemini.

**Redis keys after run:**

```bash
docker compose exec redis redis-cli -n 1 KEYS "*triage:ref*"
```

```text
1) ":1:triage:ref:shell-chunk-test:Datadog:40c13715"          ← Phase 2 shell test (old)
2) ":1:triage:ref:2db76262-6b26-49b9-83ef-64384ca1cab0:GitHub:f396ea31"
3) ":1:triage:ref:2db76262-6b26-49b9-83ef-64384ca1cab0:Slack:05233f54"
4) ":1:triage:ref:2db76262-6b26-49b9-83ef-64384ca1cab0:Datadog:cd9918fa"
```

**Read blob (pickle in CLI — JSON visible inside):**

```bash
docker compose exec redis redis-cli -n 1 GET ":1:triage:ref:2db76262-6b26-49b9-83ef-64384ca1cab0:GitHub:f396ea31"
# Contains: "recent_commit": "Update nginx config", "author": "katrina@newhire.com"
```

Pretty read in Django shell:

```python
from django.core.cache import cache
ref = "2db76262-6b26-49b9-83ef-64384ca1cab0:GitHub:f396ea31"
print(cache.get(f"triage:ref:{ref}"))
```

After Mode B testing, reset `.env`: `TRIAGE_MAX_INLINE_CHARS=8000` and recreate Celery.

---

### MCP HR daemon logs (Phase 3)

```text
INFO: POST /messages/?session_id=... HTTP/1.1 202 Accepted
Processing request of type CallToolRequest
Processing request of type ListToolsRequest
```

Confirms LangGraph called `get_employee_manager` over SSE.

---

## Phase 3 checklist

| Step | Mode A (8000) | Mode B (50) |
|------|---------------|-------------|
| 3 fetchers succeed | ✓ | ✓ |
| `CHUNKING SUMMARY` in Celery | all `inline (full)` | all `TRUNCATED → Redis` |
| Redis `triage:ref:<run_id>:*` | none new | 3 keys |
| SSE MCP 200 | ✓ | ✓ |
| Gemini + final report | ✓ | ✓ |
| Task succeeded | ✓ | ✓ |

---

## Phase 4 — Checkpoints (JSON API)

Poll all checkpoints after a run completes. Uses `events.py` → Redis list `triage:run:<run_id>:events`.

### Command

```bash
curl -s http://localhost:8000/api/v1/incidents/runs/<run_id>/events/ | python3 -m json.tool
```

Use the `run_id` from `trigger_incident_workflow()`:

```python
result = trigger_incident_workflow()
# Orchestration Canvas launched! ... Run ID: a7f96583-5577-4706-a2a6-f2d53422e89f
```

### Verified output (`run_id=a7f96583-5577-4706-a2a6-f2d53422e89f`, `TRIAGE_MAX_INLINE_CHARS=50`)

```json
{
    "run_id": "a7f96583-5577-4706-a2a6-f2d53422e89f",
    "events": [
        {
            "stage": "triage.start",
            "message": "Starting triage for srv-production-01",
            "server_id": "srv-production-01",
            "mcp_transport": "sse"
        },
        { "stage": "fetch.complete", "message": "Context gathered from parallel fetchers" },
        {
            "stage": "chunk.complete",
            "message": "Telemetry payloads prepared for LLM context limits",
            "sources": [
                {
                    "source": "Datadog",
                    "truncated": true,
                    "total_chars": 71,
                    "chunk_count": 1,
                    "inline_chars": 176,
                    "reference_id": "a7f96583-5577-4706-a2a6-f2d53422e89f:Datadog:81324193"
                },
                {
                    "source": "GitHub",
                    "truncated": true,
                    "total_chars": 101,
                    "chunk_count": 1,
                    "inline_chars": 175,
                    "reference_id": "a7f96583-5577-4706-a2a6-f2d53422e89f:GitHub:2eea65ef"
                },
                {
                    "source": "Slack",
                    "truncated": true,
                    "total_chars": 93,
                    "chunk_count": 1,
                    "inline_chars": 172,
                    "reference_id": "a7f96583-5577-4706-a2a6-f2d53422e89f:Slack:5e72f28b"
                }
            ]
        },
        { "stage": "mcp.connect", "message": "Connecting to HR MCP server" },
        { "stage": "mcp.tools", "message": "MCP tools ready (1 available)", "tool_count": 1 },
        { "stage": "agent.invoke", "message": "LangGraph ReAct agent running" },
        { "stage": "agent.complete", "message": "Agent finished reasoning loop" },
        {
            "stage": "triage.complete",
            "message": "Incident report ready",
            "report_preview": "...GitHub log entry is truncated..."
        }
    ]
}
```

### Stage reference

| `stage` | Proves |
|---------|--------|
| `triage.start` | Callback started; shows `mcp_transport: sse` |
| `fetch.complete` | Chord logs received |
| `chunk.complete` | Chunking ran; **`sources`** mirrors Celery `CHUNKING SUMMARY` |
| `mcp.connect` / `mcp.tools` | SSE MCP client connected |
| `agent.invoke` / `agent.complete` | LangGraph + Gemini loop |
| `triage.complete` | Final report; `report_preview` first 500 chars |

### Raw Redis (same data, reverse order)

```bash
docker compose exec redis redis-cli -n 1 LRANGE "triage:run:a7f96583-5577-4706-a2a6-f2d53422e89f:events" 0 -1
```

| API `/events/` | Redis `LRANGE` |
|----------------|----------------|
| Oldest event **first** | Newest event **first** (LPUSH) |
| JSON array, pretty-printed | One JSON string per line |

Entry `6` in LRANGE = `chunk.complete` with full `sources` array — matches the API.

### Phase 4 checklist

| Check | Pass? |
|-------|-------|
| 8 events returned | ✓ |
| `chunk.complete.sources` has 3 entries with `truncated: true` | ✓ |
| `reference_id` matches Celery chunk summary | ✓ |
| `triage.complete.report_preview` present | ✓ |

### Note — aggressive truncate (`limit=50`) vs agent quality

With only 50 chars inline, Gemini may report *"GitHub log is truncated… author not available"* even though the **full JSON is in Redis** (including `katrina@newhire.com`). That is expected until **`read_triage_chunk` MCP tool** (Phase 2 chunking roadmap) lets the agent pull the rest. For normal runs use `TRIAGE_MAX_INLINE_CHARS=8000`.

---

## Phase 5 — Live checkpoint stream (SSE)

Real-time checkpoints while triage runs. Same events as Phase 4, delivered as `data: {...}` lines.

### Option A — stream after trigger (replay + tail)

```bash
curl -N http://localhost:8000/api/v1/incidents/runs/<run_id>/stream/
```

Works if started within ~30s of trigger — replays history, then live events until `triage.complete`.

### Option B — live demo (recommended)

**1. Shell — create `run_id` first:**

```python
import uuid
run_id = str(uuid.uuid4())
print(run_id)
# 4235fbe3-6786-4707-a6c0-334b429b911d
```

**2. Terminal C — start stream before chord finishes:**

```bash
curl -N http://localhost:8000/api/v1/incidents/runs/4235fbe3-6786-4707-a6c0-334b429b911d/stream/
```

**3. Shell — trigger with same `run_id`:**

```python
from celery import group, chord
from apps.incidents.tasks import (
    fetch_datadog_metrics,
    fetch_github_commits,
    fetch_slack_alerts,
    run_mcp_enhanced_triage,
)

server_id = "srv-production-01"
chord(group(
    fetch_datadog_metrics.s(server_id),
    fetch_github_commits.s(server_id),
    fetch_slack_alerts.s(server_id),
))(run_mcp_enhanced_triage.s(server_id, run_id))
```

### Verified stream output (`run_id=4235fbe3-6786-4707-a6c0-334b429b911d`)

```text
data: {"ts": ..., "stage": "triage.start", "message": "Starting triage for srv-production-01", "mcp_transport": "sse"}

data: {"stage": "fetch.complete", ...}

data: {"stage": "chunk.complete", "sources": [{"source": "Datadog", "truncated": true, ...}, ...]}

data: {"stage": "mcp.connect", ...}
data: {"stage": "mcp.tools", "tool_count": 1, ...}
data: {"stage": "agent.invoke", ...}

data: {"stage": "agent.complete", ...}

data: {"stage": "triage.complete", "report_preview": "...GitHub... truncated..."}
```

Stream **closes** after `triage.complete` (by design in `TriageRunStreamView`).

### `curl: (56) chunk hex-length char not a hex digit: 0x48`

You may see this **after** all events printed:

```text
curl: (56) chunk hex-length char not a hex digit: 0x48
```

| Item | Explanation |
|------|-------------|
| **Cause** | Server closed the SSE connection when stream ended; some `curl` versions warn on the final chunk |
| **Impact** | **None** — all 8 `data:` lines were delivered; flow completed |
| **Pass?** | Yes, if you saw `triage.complete` |

Alternative: use browser DevTools → Network → EventStream, or `events/` JSON API for polling.

### Verify Phase 5 via Redis (same run)

```bash
docker compose exec redis redis-cli -n 1 LRANGE "triage:run:4235fbe3-6786-4707-a6c0-334b429b911d:events" 0 -1
```

8 entries — matches stream event count.

### Celery tail (same run, ~19s)

```text
--- CHUNKING SUMMARY ---
  [Datadog/GitHub/Slack] TRUNCATED → Redis | ...
GET http://workstack_mcp_hr:8080/sse "HTTP/1.1 200 OK"
Gemini 503 → retry → 200 OK
--- FINAL AI AGENT OUTPUT ---
I cannot identify the employee... GitHub log is truncated...
Task ... run_mcp_enhanced_triage succeeded in 18.61s
```

### Phase 5 checklist

| Check | Pass? |
|-------|-------|
| Stream shows events in order (start → complete) | ✓ |
| `chunk.complete` includes `sources` array live | ✓ |
| Stream ends on `triage.complete` | ✓ |
| Redis LRANGE has 8 events for same `run_id` | ✓ |
| `curl (56)` after complete | Harmless |

---

---

## Phase 6 — `read_triage_chunk` MCP tool

**What ships:** `mcp_daemons/triage_server.py` — a second MCP daemon with two tools:

| Tool | Purpose |
|------|---------|
| `read_triage_chunk(reference_id, chunk_index)` | Pull a 4 000-char slice of a large stored payload |
| `list_triage_references(run_id)` | Discover what reference_ids exist for a run |

When a source is truncated the agent now sees an explicit instruction in the prompt:

```
IMPORTANT — TRUNCATED SOURCES: GitHub
...
You MUST use the read_triage_chunk tool to retrieve the full content before drawing
conclusions. Start with chunk_index=0.
```

---

### 6-A  Unit tests (no daemon needed)

These tests hit Redis directly via `store_reference` / `get_chunk`, the same path
`triage_server.py` uses internally.

```bash
docker compose exec web python manage.py test apps.incidents.tests.test_chunking -v 2
```

**Expected output:**

```
test_all_chunks_reassemble_full_text ... ok
test_chunk_count_matches_ceiling_division ... ok
test_chunk_one_returns_second_slice ... ok
test_chunk_zero_returns_first_slice ... ok
test_last_chunk_does_not_overflow ... ok
test_missing_reference_returns_error_message ... ok
test_out_of_range_chunk_returns_error_message ... ok
test_triage_ref_marker_contains_chunk_count ... ok
test_triage_ref_marker_contains_reference_id ... ok
...
Ran 12 tests in 0.XXXs
OK
```

---

### 6-B  Start the triage MCP daemon

```bash
# Re-build to pick up the new triage_server.py
docker compose up -d --build mcp_triage_daemon

# Verify it started
docker compose logs mcp_triage_daemon
# Expected: "Starting MCP Triage Daemon on port 8090..."

# Check SSE endpoint responds
curl -s --max-time 3 http://localhost:8090/sse
# Expected: keeps connection open (event stream); Ctrl-C to break
```

---

### 6-C  Verify tools are registered

```bash
docker compose exec web python manage.py shell
```

```python
import asyncio
from apps.incidents.mcp_client import build_mcp_client

client = build_mcp_client()
tools = asyncio.run(client.get_tools())
print([t.name for t in tools])
# Expected (order may vary):
# ['get_employee_manager', 'read_triage_chunk', 'list_triage_references']
```

---

### 6-D  Shell exercise: store a reference, then call the tool

```python
# Inside docker compose exec web python manage.py shell
from django.test.utils import override_settings

with override_settings(TRIAGE_MAX_INLINE_CHARS=50, TRIAGE_CHUNK_SIZE=40, TRIAGE_REFERENCE_TTL=300):
    from apps.incidents.chunking import prepare_payload_for_prompt, get_chunk

    payload = {
        "source": "GitHub",
        "recent_commit": "Update nginx config",
        "author": "katrina@newhire.com",
        "files_changed": ["nginx.conf", "docker-compose.yml", "README.md"],
    }

    result = prepare_payload_for_prompt("GitHub", payload, "shell-phase6")
    print("truncated:", result.truncated)
    print("chunks   :", result.chunk_count)
    print("ref id   :", result.reference_id)
    print("inline   :\n", result.inline[:200])
    print()

    # Now simulate what the MCP tool does
    for i in range(result.chunk_count):
        chunk = get_chunk(result.reference_id, i)
        print(f"--- chunk {i} ({len(chunk)} chars) ---")
        print(chunk[:120])
```

**Actual output (from real run):**

```
truncated: True
chunks   : 5
ref id   : shell-phase6:GitHub:894d9f4d
inline   :
 {
  "source": "GitHub",
  "recent_commit": "Update

[TRIAGE_REF source=GitHub id=shell-phase6:GitHub:894d9f4d total_chars=187 chunks=5 inline_limit=50]

--- chunk 0 (40 chars) ---
{
  "source": "GitHub",
  "recent_commit
--- chunk 1 (40 chars) ---
": "Update nginx config",
  "author": "k
--- chunk 2 (40 chars) ---
atrina@newhire.com",
  "files_changed":
--- chunk 3 (40 chars) ---
[
    "nginx.conf",
    "docker-compose.
--- chunk 4 (27 chars) ---
yml",
    "README.md"
  ]
}
```

> **Why 5 chunks, not 4?** The actual serialised JSON is 187 chars. `ceil(187 / 40) = 5`. The number depends on the exact JSON Django produces (indentation, field order). The inline limit (50 chars) only controls what sits in the prompt — all 5 chunks cover the full payload.
>
> **Where is the author?** `"katrina@newhire.com"` starts mid-way through chunk 1 and finishes in chunk 2. Gemini must call `read_triage_chunk` at least twice to find it.

---

### 6-E  Full flow: force `read_triage_chunk` to be called

#### Why the previous run did NOT call `read_triage_chunk`

The old mock payload was:
```python
{"source": "GitHub", "recent_commit": "Update nginx config", "author": "katrina@newhire.com"}
```
Serialised JSON: **101 chars**. With `CHUNK_SIZE=40`, the author `katrina@newhire.com` sits in chunk **1** (chars 40–79). Gemini finds it in one or two calls and stops — no multi-chunk excavation.

Additionally, changing the code defaults in `chunking.py` does nothing because Django reads the value from settings first (`getattr(settings, "TRIAGE_MAX_INLINE_CHARS", ...)`), which is populated from `.env` via `base.py`. The code defaults only apply when no setting or env var exists at all.

#### What was fixed

`fetch_github_commits` now returns a structured payload where `"author"` is deliberately placed past char 163:

```python
{
    "source": "GitHub",
    "status": "success",
    "pipeline": ["lint", "test", "build", "deploy"],
    "recent_commit": "Update nginx config",
    "author": "katrina@newhire.com",   # char 163 → chunk 4
}
```

Serialised (195 chars total, `CHUNK_SIZE=40`, `INLINE_LIMIT=50`):

```
Inline (0–50 chars, sent in prompt):
  '{\n  "source": "GitHub",\n  "status": "success",\n  "'
  ↑ cuts mid-word — author is nowhere here

chunk 0  (chars   0– 39): '{\n  "source": "GitHub",\n  "status": "suc'
chunk 1  (chars  40– 79): 'cess",\n  "pipeline": [\n    "lint",\n    "'
chunk 2  (chars  80–119): 'test",\n    "build",\n    "deploy"\n  ],\n  '
chunk 3  (chars 120–159): '"recent_commit": "Update nginx config",\n'
chunk 4  (chars 160–194): '  "author": "katrina@newhire.com"\n}'  ← HERE
```

Gemini must call `read_triage_chunk` **5 times** (chunks 0–4) before it finds the author.

---

#### Prerequisites — `.env` already configured

Your `.env` already has:
```
TRIAGE_MAX_INLINE_CHARS=50
TRIAGE_CHUNK_SIZE=40
```

Restart Celery to pick up the new mock payload code:

```bash
docker compose restart celery
docker compose logs celery --tail=10
# Expected: "celery@... ready"
```

---

#### Step 1 — pre-pick a `run_id` and open the live stream

Open **Terminal 1** (SSE stream — must be open BEFORE the chord fires):

```bash
export RUN_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
echo "run_id: $RUN_ID"
curl -N http://localhost:8000/api/v1/incidents/runs/$RUN_ID/stream/
```

---

#### Step 2 — trigger the chord with the same `run_id`

Open **Terminal 2**:

```bash
docker compose exec web python manage.py shell
```

```python
# trigger_incident_workflow() is a plain function — not a @shared_task.
# It builds the chord internally. Do NOT use .delay() on it.
# To supply your own run_id, build the chord manually:

from celery import group, chord
from apps.incidents.tasks import (
    fetch_datadog_metrics,
    fetch_github_commits,
    fetch_slack_alerts,
    run_mcp_enhanced_triage,
)

run_id = "<paste $RUN_ID from Terminal 1>"
server_id = "srv-production-01"

result = chord(
    group(
        fetch_datadog_metrics.s(server_id),
        fetch_github_commits.s(server_id),
        fetch_slack_alerts.s(server_id),
    )
)(run_mcp_enhanced_triage.s(server_id, run_id))

print("chord launched, task id:", result.id)
```

---

#### Step 3 — watch the SSE stream (Terminal 1)

You will see events arrive in order:

```
data: {"stage": "triage.start",   "message": "Starting triage for srv-production-01"}
data: {"stage": "fetch.complete",  "message": "Context gathered from parallel fetchers"}
data: {"stage": "chunk.complete",  "message": "...", "sources": [
         {"source": "GitHub", "truncated": true, "chunk_count": 5}   ← truncated
       ]}
data: {"stage": "mcp.connect",    "message": "Connecting to MCP servers (HR + Triage)"}
data: {"stage": "mcp.tools",      "tool_count": 3, "tool_names": [...]}
data: {"stage": "agent.invoke",   "message": "LangGraph ReAct agent running"}
... (Gemini calls read_triage_chunk 5× then get_employee_manager) ...
data: {"stage": "agent.complete", "message": "Agent finished reasoning loop"}
data: {"stage": "triage.complete","report_preview": "..."}
```

---

#### Step 4 — watch Celery logs (Terminal 3)

```bash
docker compose logs -f celery
```

You should see the ReAct loop making **5 chunk calls then 1 HR call**:

```
Processing request of type CallToolRequest  ← read_triage_chunk chunk 0
Processing request of type CallToolRequest  ← read_triage_chunk chunk 1
Processing request of type CallToolRequest  ← read_triage_chunk chunk 2
Processing request of type CallToolRequest  ← read_triage_chunk chunk 3
Processing request of type CallToolRequest  ← read_triage_chunk chunk 4  (author found)
Processing request of type CallToolRequest  ← get_employee_manager
```

---

#### Step 5 — verify events JSON

```bash
curl -s "http://localhost:8000/api/v1/incidents/runs/<run_id>/events/" | python3 -m json.tool
```

The `chunk.complete` event should confirm GitHub was truncated:

```json
{
  "stage": "chunk.complete",
  "sources": [
    {"source": "Datadog", "truncated": false, "total_chars": 71,  "chunk_count": 1},
    {"source": "GitHub",  "truncated": true,  "total_chars": 195, "chunk_count": 5},
    {"source": "Slack",   "truncated": false, "total_chars": 93,  "chunk_count": 1}
  ]
}
```

---

#### Step 6 — reset env after testing

```bash
# Remove or update these lines in .env:
TRIAGE_MAX_INLINE_CHARS=8000
TRIAGE_CHUNK_SIZE=4000

docker compose restart celery
```

> **Note:** The new `fetch_github_commits` payload stays in place — it is more realistic than the original one-liner and still fits inline under the production `TRIAGE_MAX_INLINE_CHARS=8000` default, so normal runs are unaffected.

---

### 6-F  Preflight check — verify triage SSE is reachable

```python
# docker compose exec web python manage.py shell
import urllib.request
try:
    r = urllib.request.urlopen("http://workstack_mcp_triage:8090/sse", timeout=3)
    print("reachable — status", r.status)
except Exception as e:
    print("NOT reachable:", e)
```

---

### Phase 6 checklist

| Check | Expected | Verified |
|-------|----------|----------|
| All 12 unit tests pass | `Ran 12 tests ... OK` | |
| `mcp_triage_daemon` starts on :8090 | `Starting MCP Triage Daemon` log line | |
| `get_tools()` returns 3 tools | `read_triage_chunk` in list | ✓ `tool_count: 3` confirmed |
| `mcp.tools` checkpoint has `tool_count=3` | JSON event | ✓ `5dab89bb` run |
| Shell 6-D: chunks=5 for 187-char payload | `ceil(187/40)=5` | ✓ `894d9f4d` ref |
| Agent calls `read_triage_chunk` when truncated | Celery log: 2+ chunk calls before `get_employee_manager` | → Run 6-E to verify |

---

## All phases checklist

| Phase | What | Status |
|-------|------|--------|
| **1** | Preflight (`test_mcp_sse`, `test_chunking`) | |
| **2** | Shell chunking A + B | |
| **3** | Celery → LangGraph → MCP → Gemini | |
| **4** | `/events/` JSON + `chunk.complete.sources` | |
| **5** | `/stream/` live SSE | |
| **6** | `read_triage_chunk` tool + agent auto-reads truncated sources | |

---

## Notes

- `log_chunking_summary` is commented out in `tasks.py` — uncomment the line to
  re-enable debug output while testing locally.
- Reset `TRIAGE_MAX_INLINE_CHARS=8000` in `.env` after truncation testing.

---

[← Run guide](INCIDENT_TRIAGE_AGENT.md) · [← README](../README.md)
