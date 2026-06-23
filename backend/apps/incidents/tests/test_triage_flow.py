"""
Tests for the incident triage agent (Celery fetchers → LangGraph → MCP).

Run all tests (unit tests always run; integration skips without API key):

    python manage.py test apps.incidents.tests.test_triage_flow -v 2

Integration test environment:
    GEMINI_API_KEY         Required for test_full_triage_flow
    INCIDENT_TEST_EMAIL    Optional; must match fetch_github_commits author in DB
"""
import asyncio
import os
import unittest

from langchain_core.messages import AIMessage

from apps.incidents.parser import extract_message_text
from apps.incidents.tasks import (
    fetch_datadog_metrics,
    fetch_github_commits,
    fetch_slack_alerts,
    run_mcp_enhanced_triage,
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MCP_SSE_URL = os.environ.get("MCP_SSE_URL", "http://workstack_mcp_hr:8080/sse")
INCIDENT_TEST_SERVER = "srv-production-01"
GITHUB_AUTHOR = "katrina@newhire.com"


def _mcp_sse_reachable() -> tuple[bool, str]:
    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async def ping():
            async with sse_client(MCP_SSE_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

        asyncio.run(ping())
        return True, ""
    except Exception as exc:
        return False, (
            f"MCP SSE daemon not reachable at {MCP_SSE_URL}: {exc}. "
            "Start with: docker compose up mcp_hr_daemon"
        )


class ExtractMessageTextTest(unittest.TestCase):
    """Unit tests for Gemini block → plain string normalization."""

    def test_plain_string(self):
        self.assertEqual(extract_message_text("hello"), "hello")

    def test_none_and_empty(self):
        self.assertEqual(extract_message_text(None), "")
        self.assertEqual(extract_message_text(""), "")

    def test_text_block_list(self):
        content = [
            {
                "type": "text",
                "text": "Emergency Incident Report:\n\n**Server:** srv-production-01",
                "extras": {"signature": "abc"},
            }
        ]
        self.assertIn("Emergency Incident Report", extract_message_text(content))
        self.assertNotIn("signature", extract_message_text(content))

    def test_multiple_text_blocks(self):
        content = [
            {"type": "text", "text": "Line 1"},
            {"type": "text", "text": "Line 2"},
        ]
        self.assertEqual(extract_message_text(content), "Line 1\nLine 2")

    def test_mixed_string_and_dict_blocks(self):
        content = ["Prefix:", {"type": "text", "text": "Body"}]
        self.assertEqual(extract_message_text(content), "Prefix:\nBody")

    def test_from_aimessage(self):
        message = AIMessage(
            content=[{"type": "text", "text": "Incident resolved."}]
        )
        self.assertEqual(extract_message_text(message.content), "Incident resolved.")


class IncidentFetcherTest(unittest.TestCase):
    """Deterministic fetchers — no Gemini, no MCP."""

    def test_fetchers_return_expected_shape(self):
        server_id = INCIDENT_TEST_SERVER
        datadog = fetch_datadog_metrics(server_id)
        github = fetch_github_commits(server_id)
        slack = fetch_slack_alerts(server_id)

        self.assertEqual(datadog["source"], "Datadog")
        self.assertEqual(datadog["status"], "critical")
        self.assertEqual(github["source"], "GitHub")
        self.assertEqual(github["author"], GITHUB_AUTHOR)
        self.assertEqual(slack["source"], "Slack")
        self.assertIn("502 Bad Gateway", slack["messages"])

    def test_aggregated_logs_match_chord_input(self):
        """Chord passes a list of fetcher return values as the first callback arg."""
        aggregated = [
            fetch_datadog_metrics(INCIDENT_TEST_SERVER),
            fetch_github_commits(INCIDENT_TEST_SERVER),
            fetch_slack_alerts(INCIDENT_TEST_SERVER),
        ]
        sources = {entry["source"] for entry in aggregated}
        self.assertEqual(sources, {"Datadog", "GitHub", "Slack"})
        authors = [
            entry["author"]
            for entry in aggregated
            if entry.get("source") == "GitHub"
        ]
        self.assertEqual(authors, [GITHUB_AUTHOR])


class IncidentTriageIntegrationTest(unittest.TestCase):
    """End-to-end: aggregated logs → LangGraph ReAct → MCP (SSE default) → Gemini report."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not GEMINI_API_KEY:
            cls.gemini_available = False
            cls.skip_reason = "GEMINI_API_KEY is not set"
            return
        reachable, reason = _mcp_sse_reachable()
        if not reachable:
            cls.gemini_available = False
            cls.skip_reason = reason
            return
        cls.gemini_available = True

    def setUp(self):
        if not self.gemini_available:
            self.skipTest(self.skip_reason)

    def test_full_triage_flow(self):
        aggregated_logs = [
            fetch_datadog_metrics(INCIDENT_TEST_SERVER),
            fetch_github_commits(INCIDENT_TEST_SERVER),
            fetch_slack_alerts(INCIDENT_TEST_SERVER),
        ]

        result = run_mcp_enhanced_triage(aggregated_logs, INCIDENT_TEST_SERVER, "test-run")

        self.assertIsInstance(result, dict)
        self.assertIn("report", result)
        self.assertIn("run_id", result)
        report = result["report"]
        self.assertIsInstance(report, str)
        self.assertGreater(len(report), 100)
        lowered = report.lower()
        self.assertTrue(
            INCIDENT_TEST_SERVER.lower() in lowered
            or "production" in lowered
            or "server" in lowered,
            msg=f"Report should reference the incident server: {report[:200]}...",
        )
        self.assertTrue(
            any(
                token in lowered
                for token in ("cpu", "nginx", "502", "latency", "critical")
            ),
            msg=f"Report should reflect pre-fetched logs: {report[:200]}...",
        )
        self.assertTrue(
            GITHUB_AUTHOR in report
            or "katrina" in lowered
            or "manager" in lowered
            or "employee" in lowered,
            msg=f"Report should mention commit author or manager lookup: {report[:200]}...",
        )
