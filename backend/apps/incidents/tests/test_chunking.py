import re

from django.test import TestCase, override_settings
from django_redis import get_redis_connection

from apps.incidents.chunking import (
    build_chunked_log_context,
    chunk_count_for,
    get_chunk,
    prepare_payload_for_prompt,
    store_reference,
)


@override_settings(
    TRIAGE_MAX_INLINE_CHARS=100,
    TRIAGE_CHUNK_SIZE=50,
    TRIAGE_REFERENCE_TTL=60,
)
class ChunkingTest(TestCase):
    def test_small_payload_stays_inline(self):
        payload = {"source": "Datadog", "cpu_usage": "99%"}
        result = prepare_payload_for_prompt("Datadog", payload, "run-1")
        self.assertFalse(result.truncated)
        self.assertIsNone(result.reference_id)
        self.assertIn("99%", result.inline)

    def test_large_payload_creates_reference(self):
        payload = {"source": "Datadog", "lines": ["x" * 80 for _ in range(20)]}
        result = prepare_payload_for_prompt("Datadog", payload, "run-2")
        self.assertTrue(result.truncated)
        self.assertIsNotNone(result.reference_id)
        self.assertIn("TRIAGE_REF", result.inline)
        self.assertGreater(result.chunk_count, 1)

        chunk_zero = get_chunk(result.reference_id, 0)
        self.assertGreater(len(chunk_zero), 0)

    def test_build_chunked_log_context_sections(self):
        logs = [
            {"source": "GitHub", "author": "a@b.com"},
            {"source": "Slack", "messages": ["alert"]},
        ]
        text, payloads = build_chunked_log_context(logs, "run-3")
        self.assertIn("### GitHub", text)
        self.assertIn("### Slack", text)
        self.assertEqual(len(payloads), 2)


@override_settings(
    TRIAGE_MAX_INLINE_CHARS=100,
    TRIAGE_CHUNK_SIZE=50,
    TRIAGE_REFERENCE_TTL=60,
)
class ReadTriageChunkToolTest(TestCase):
    """
    Verifies the data layer that triage_server.read_triage_chunk relies on.
    Tests store_reference / get_chunk directly so there is no I/O dependency
    on a running MCP daemon.
    """

    def _store_large_text(self) -> tuple[str, str]:
        """Return (reference_id, full_text) for a payload that spans 3+ chunks."""
        full_text = "A" * 160  # 160 chars → ceil(160/50)=4 chunks at CHUNK_SIZE=50
        ref_id = store_reference(full_text, "read-chunk-run", "GitHub")
        return ref_id, full_text

    def test_chunk_zero_returns_first_slice(self):
        ref_id, full_text = self._store_large_text()
        chunk = get_chunk(ref_id, 0)
        self.assertEqual(chunk, full_text[:50])

    def test_chunk_one_returns_second_slice(self):
        ref_id, full_text = self._store_large_text()
        chunk = get_chunk(ref_id, 1)
        self.assertEqual(chunk, full_text[50:100])

    def test_last_chunk_does_not_overflow(self):
        ref_id, full_text = self._store_large_text()
        total = chunk_count_for(ref_id)
        last_chunk = get_chunk(ref_id, total - 1)
        self.assertGreater(len(last_chunk), 0)
        self.assertLessEqual(len(last_chunk), 50)

    def test_out_of_range_chunk_returns_error_message(self):
        ref_id, _ = self._store_large_text()
        result = get_chunk(ref_id, 99)
        self.assertIn("out of range", result)
        self.assertIn(ref_id, result)

    def test_missing_reference_returns_error_message(self):
        result = get_chunk("nonexistent-ref-id", 0)
        self.assertIn("expired or not found", result)

    def test_chunk_count_matches_ceiling_division(self):
        full_text = "B" * 175  # ceil(175/50) = 4 chunks
        ref_id = store_reference(full_text, "count-run", "Slack")
        self.assertEqual(chunk_count_for(ref_id), 4)

    def test_all_chunks_reassemble_full_text(self):
        """Reassembling all chunks must reproduce the original payload."""
        full_text = "C" * 213  # 5 chunks
        ref_id = store_reference(full_text, "reassemble-run", "Datadog")
        total = chunk_count_for(ref_id)
        assembled = "".join(get_chunk(ref_id, i) for i in range(total))
        self.assertEqual(assembled, full_text)

    def test_triage_ref_marker_contains_reference_id(self):
        """TRIAGE_REF inline marker must embed the stored reference_id."""
        payload = {"source": "GitHub", "lines": ["x" * 200]}
        result = prepare_payload_for_prompt("GitHub", payload, "marker-run")
        self.assertIn("TRIAGE_REF", result.inline)
        self.assertIn(result.reference_id, result.inline)

    def test_triage_ref_marker_contains_chunk_count(self):
        payload = {"source": "GitHub", "lines": ["x" * 200]}
        result = prepare_payload_for_prompt("GitHub", payload, "marker-run-2")
        # marker looks like: chunks=N
        match = re.search(r"chunks=(\d+)", result.inline)
        self.assertIsNotNone(match, "TRIAGE_REF marker should contain chunks=N")
        self.assertGreater(int(match.group(1)), 1)
