"""
Triage MCP Server — tools that let the LangGraph agent pull full log payloads
stored in Redis by chunking.py.

Without this server the agent only sees the first TRIAGE_MAX_INLINE_CHARS of
each source. With it the ReAct loop can call read_triage_chunk() to retrieve
subsequent slices — so it finds commit authors, stack traces, etc. even when
logs are large.

Transports:
  python triage_server.py               → SSE daemon on :8090 (Docker default)
  python triage_server.py --transport stdio  → stdio subprocess (local dev)
"""
import argparse
import os
import sys

from mcp.server.fastmcp import FastMCP

# ── Standalone boot (same pattern as hr_server.py) ──────────────────────────
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local")

import django  # noqa: E402
django.setup()

from django.core.cache import cache  # noqa: E402  — needs django.setup() first


_CACHE_KEY_PREFIX = "triage:ref:"
_DEFAULT_CHUNK_SIZE = int(os.environ.get("TRIAGE_CHUNK_SIZE", "4000"))


def _get_chunk_size() -> int:
    try:
        from django.conf import settings
        return int(getattr(settings, "TRIAGE_CHUNK_SIZE", _DEFAULT_CHUNK_SIZE))
    except Exception:
        return _DEFAULT_CHUNK_SIZE


def _register_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def read_triage_chunk(reference_id: str, chunk_index: int) -> str:
        """
        Read a slice of a large log payload stored during triage chunking.

        Use this tool when you see a [TRIAGE_REF ...] marker in the logs.
        The marker tells you the reference_id and total number of chunks.
        Call this tool with chunk_index=0 for the first slice, 1 for the next,
        and so on until you find the information you need.

        Args:
            reference_id: The id from the [TRIAGE_REF id=...] marker.
            chunk_index: Zero-based index (0 = first chunk, 1 = second, ...).

        Returns:
            The requested text slice, or an error/expiry message.
        """
        full_text = cache.get(f"{_CACHE_KEY_PREFIX}{reference_id}")
        if full_text is None:
            return (
                f"[triage_chunk] Reference '{reference_id}' not found or expired. "
                "The payload may have exceeded its TTL (1 hour by default)."
            )

        size = _get_chunk_size()
        start = chunk_index * size
        if start >= len(full_text):
            total_chunks = max(1, -(-len(full_text) // size))  # ceil division
            return (
                f"[triage_chunk] chunk_index={chunk_index} is out of range. "
                f"Reference '{reference_id}' has {total_chunks} chunk(s) "
                f"(total {len(full_text)} chars)."
            )

        return full_text[start : start + size]

    @mcp.tool()
    def list_triage_references(run_id: str) -> str:
        """
        List all stored chunk references for a triage run.

        Use this to discover what reference_ids are available for a given run_id
        before calling read_triage_chunk.

        Args:
            run_id: The triage run UUID (shown in the [TRIAGE_REF id=...] marker
                    as the first part before the source name).

        Returns:
            A summary of all references stored for this run, with chunk counts.
        """
        try:
            from django_redis import get_redis_connection
            conn = get_redis_connection("default")
            # Django cache stores keys with :1: version prefix
            pattern = f":1:{_CACHE_KEY_PREFIX}{run_id}:*"
            keys = conn.keys(pattern)
        except Exception as e:
            return f"[triage_refs] Error listing references: {e}"

        if not keys:
            return (
                f"[triage_refs] No references found for run_id={run_id}. "
                "The run may not have had any truncated payloads, "
                "or the TTL (1h) may have expired."
            )

        lines = [f"References for run {run_id}:"]
        for raw_key in keys:
            key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
            # Strip Django version prefix: :1:triage:ref:<ref_id>
            ref_id = key.removeprefix(f":1:{_CACHE_KEY_PREFIX}")
            full_text = cache.get(f"{_CACHE_KEY_PREFIX}{ref_id}")
            if full_text:
                size = _get_chunk_size()
                total_chunks = max(1, -(-len(full_text) // size))
                lines.append(
                    f"  reference_id={ref_id} "
                    f"total_chars={len(full_text)} chunks={total_chunks}"
                )
        return "\n".join(lines)


def run_stdio():
    mcp = FastMCP("Workstack_Triage_Daemon")
    _register_tools(mcp)
    mcp.run()


def run_sse():
    mcp = FastMCP("Workstack_Triage_Daemon", host="0.0.0.0", port=8090)
    _register_tools(mcp)
    print("Starting MCP Triage Daemon on port 8090...", file=sys.stderr)
    mcp.run(transport="sse")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Workstack Triage MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="sse",
        help="stdio for subprocess; sse for persistent daemon (default: sse)",
    )
    args = parser.parse_args()
    if args.transport == "stdio":
        run_stdio()
    else:
        run_sse()
