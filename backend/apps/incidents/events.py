"""
Triage run checkpoints for live UI (Redis list + pub/sub).

Subscribe from the browser via GET /api/v1/incidents/runs/<run_id>/stream/
"""
import json
import time

from django_redis import get_redis_connection

EVENT_LIST_MAX = 200
RUN_TTL_SECONDS = 3600


def _events_key(run_id: str) -> str:
    return f"triage:run:{run_id}:events"


def _channel(run_id: str) -> str:
    return f"triage:run:{run_id}"


def publish_checkpoint(run_id: str, stage: str, message: str, **meta) -> None:
    event = {
        "ts": time.time(),
        "stage": stage,
        "message": message,
        **meta,
    }
    payload = json.dumps(event)
    conn = get_redis_connection("default")
    key = _events_key(run_id)
    conn.lpush(key, payload)
    conn.ltrim(key, 0, EVENT_LIST_MAX - 1)
    conn.expire(key, RUN_TTL_SECONDS)
    conn.publish(_channel(run_id), payload)


def list_checkpoints(run_id: str) -> list[dict]:
    conn = get_redis_connection("default")
    raw = conn.lrange(_events_key(run_id), 0, EVENT_LIST_MAX - 1)
    events = [json.loads(item) for item in raw]
    events.reverse()
    return events
