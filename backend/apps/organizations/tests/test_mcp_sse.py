"""
Integration tests for the MCP HR SSE daemon (mcp_daemons/hr_server.py).

These tests require the workstack_mcp_hr container to be running:

    docker compose up mcp_hr_daemon

Run:

    python manage.py test apps.organizations.tests.test_mcp_sse -v 2

Environment variables:
    MCP_SSE_URL          Default: http://workstack_mcp_hr:8080/sse
    MCP_SSE_TEST_EMAIL   Default: shuaib@workstack.dev (must match User.username in DB)
"""
import asyncio
import os
import unittest

from mcp import ClientSession
from mcp.client.sse import sse_client


MCP_SSE_URL = os.environ.get("MCP_SSE_URL", "http://workstack_mcp_hr:8080/sse")
MCP_SSE_TEST_EMAIL = os.environ.get("MCP_SSE_TEST_EMAIL", "shuaib@workstack.dev")


async def _call_get_employee_manager(url: str, email: str) -> str:
    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "get_employee_manager",
                {"email": email},
            )
            return result.content[0].text


class MCPSSEIntegrationTest(unittest.TestCase):
    """Proves Client → SSE daemon → PostgreSQL without Gemini."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        async def _ping_daemon():
            async with sse_client(MCP_SSE_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

        try:
            asyncio.run(_ping_daemon())
            cls.daemon_available = True
        except Exception as exc:
            cls.daemon_available = False
            cls.skip_reason = (
                f"MCP SSE daemon not reachable at {MCP_SSE_URL}: {exc}. "
                "Start it with: docker compose up mcp_hr_daemon"
            )

    def setUp(self):
        if not self.daemon_available:
            self.skipTest(self.skip_reason)

    def test_sse_connection_and_get_employee_manager(self):
        """Manual tool call over HTTP/SSE — same path Gemini uses via call_tool."""
        output = asyncio.run(
            _call_get_employee_manager(MCP_SSE_URL, MCP_SSE_TEST_EMAIL)
        )

        self.assertIsInstance(output, str)
        self.assertTrue(
            output.startswith("Manager:") or output.startswith("Error:"),
            msg=f"Unexpected tool output: {output}",
        )
        if output.startswith("Manager:"):
            self.assertIn("(", output)
            self.assertIn(")", output)

    def test_sse_handshake_only(self):
        async def handshake():
            async with sse_client(MCP_SSE_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = [t.name for t in tools.tools]
                    return names

        tool_names = asyncio.run(handshake())
        self.assertIn("get_employee_manager", tool_names)
