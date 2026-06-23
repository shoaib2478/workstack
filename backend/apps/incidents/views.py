import json
import time

from django.http import JsonResponse, StreamingHttpResponse
from django.views import View
from django_redis import get_redis_connection

from apps.incidents.events import _channel, _events_key, list_checkpoints


class TriageRunCheckpointsView(View):
    """Poll checkpoints for a triage run (JSON)."""

    def get(self, request, run_id):
        return JsonResponse({"run_id": run_id, "events": list_checkpoints(run_id)})


class TriageRunStreamView(View):
    """Server-Sent Events stream of live triage checkpoints."""

    def get(self, request, run_id):
        def event_stream():
            conn = get_redis_connection("default")
            historical = conn.lrange(_events_key(run_id), 0, -1)
            for raw in reversed(historical):
                yield f"data: {raw.decode() if isinstance(raw, bytes) else raw}\n\n"

            pubsub = conn.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(_channel(run_id))
            deadline = time.time() + 600
            try:
                while time.time() < deadline:
                    message = pubsub.get_message(timeout=5.0)
                    if message and message.get("type") == "message":
                        data = message["data"]
                        if isinstance(data, bytes):
                            data = data.decode()
                        yield f"data: {data}\n\n"
                        event = json.loads(data)
                        if event.get("stage") == "triage.complete":
                            break
            finally:
                pubsub.unsubscribe(_channel(run_id))
                pubsub.close()

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
