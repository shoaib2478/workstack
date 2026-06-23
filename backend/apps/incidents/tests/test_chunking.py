from django.test import TestCase, override_settings

from apps.incidents.chunking import (
    build_chunked_log_context,
    get_chunk,
    prepare_payload_for_prompt,
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
