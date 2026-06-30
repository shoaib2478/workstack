"""
Chunk large telemetry/log payloads before sending them to the LLM.

Full bodies are stored in Redis; the prompt gets a bounded inline summary plus a
reference id the agent (or a future MCP tool) can use to pull more chunks.
"""
import json
import math
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache


@dataclass(frozen=True)
class ChunkedPayload:
    source: str
    inline: str
    reference_id: str | None
    total_chars: int
    chunk_count: int
    truncated: bool


def _max_inline_chars() -> int:
    return int(getattr(settings, "TRIAGE_MAX_INLINE_CHARS", 8000))


def _chunk_size() -> int:
    return int(getattr(settings, "TRIAGE_CHUNK_SIZE", 4000))


def _reference_ttl() -> int:
    return int(getattr(settings, "TRIAGE_REFERENCE_TTL", 3600))


def _reference_key(reference_id: str) -> str:
    return f"triage:ref:{reference_id}"


def store_reference(full_text: str, run_id: str, source: str) -> str:
    reference_id = f"{run_id}:{source}:{uuid.uuid4().hex[:8]}"
    cache.set(_reference_key(reference_id), full_text, timeout=_reference_ttl())
    return reference_id


def get_chunk(reference_id: str, chunk_index: int) -> str:
    """Return a zero-based chunk slice for a stored reference."""
    full_text = cache.get(_reference_key(reference_id))
    if full_text is None:
        return f"[reference {reference_id} expired or not found]"

    size = _chunk_size()
    start = chunk_index * size
    if start >= len(full_text):
        return f"[chunk {chunk_index} out of range for reference {reference_id}]"
    return full_text[start : start + size]


def chunk_count_for(reference_id: str) -> int:
    full_text = cache.get(_reference_key(reference_id))
    if not full_text:
        return 0
    return max(1, math.ceil(len(full_text) / _chunk_size()))


def prepare_payload_for_prompt(source: str, payload: dict, run_id: str) -> ChunkedPayload:
    """Serialize a fetcher/tool payload and cap inline size for the LLM prompt."""
    text = json.dumps(payload, default=str, indent=2)
    total = len(text)
    max_inline = _max_inline_chars()

    if total <= max_inline:
        return ChunkedPayload(
            source=source,
            inline=text,
            reference_id=None,
            total_chars=total,
            chunk_count=1,
            truncated=False,
        )

    reference_id = store_reference(text, run_id, source)
    chunks = chunk_count_for(reference_id)
    inline_body = text[:max_inline]

    inline = (
        f"{inline_body}\n\n"
        f"[TRIAGE_REF source={source} id={reference_id} "
        f"total_chars={total} chunks={chunks} "
        f"inline_limit={max_inline}]"
    )
    return ChunkedPayload(
        source=source,
        inline=inline,
        reference_id=reference_id,
        total_chars=total,
        chunk_count=chunks,
        truncated=True,
    )


def format_chunking_summary(payloads: list[ChunkedPayload]) -> list[dict]:
    """JSON-serializable per-source chunking stats for checkpoints / tests."""
    return [
        {
            "source": p.source,
            "truncated": p.truncated,
            "total_chars": p.total_chars,
            "chunk_count": p.chunk_count,
            "inline_chars": len(p.inline),
            "reference_id": p.reference_id,
        }
        for p in payloads
    ]


def log_chunking_summary(payloads: list[ChunkedPayload]) -> None:
    """Print chunking decisions to Celery logs — for local testing; comment out in prod."""
    print("\n--- CHUNKING SUMMARY (testing — comment out in tasks.py when done) ---")
    print(
        f"TRIAGE_MAX_INLINE_CHARS={_max_inline_chars()} "
        f"TRIAGE_CHUNK_SIZE={_chunk_size()}"
    )
    for p in payloads:
        status = "TRUNCATED → Redis" if p.truncated else "inline (full)"
        print(
            f"  [{p.source}] {status} | total={p.total_chars} "
            f"inline={len(p.inline)} chunks={p.chunk_count}"
        )
        if p.reference_id:
            print(f"    reference_id={p.reference_id}")
        if "TRIAGE_REF" in p.inline:
            print(f"    marker: ...{p.inline[p.inline.index('[TRIAGE_REF'):][:120]}")
    print("--- END CHUNKING SUMMARY ---\n")


def build_chunked_log_context(
    aggregated_logs: list, run_id: str
) -> tuple[str, list[ChunkedPayload]]:
    """Turn chord results into a bounded string for the agent prompt."""
    sections = []
    payloads: list[ChunkedPayload] = []
    for entry in aggregated_logs:
        source = entry.get("source", "unknown")
        chunked = prepare_payload_for_prompt(source, entry, run_id)
        payloads.append(chunked)
        sections.append(f"### {chunked.source}\n{chunked.inline}")
    return "\n\n".join(sections), payloads
