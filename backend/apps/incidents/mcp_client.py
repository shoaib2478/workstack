import os

from django.conf import settings
from langchain_mcp_adapters.client import MultiServerMCPClient


def build_mcp_client() -> MultiServerMCPClient:
    """Build MCP client — SSE to persistent daemon (default) or stdio for local dev."""
    transport = getattr(settings, "MCP_TRANSPORT", "sse")

    if transport == "stdio":
        server_path = os.path.join(settings.BASE_DIR, "mcp_daemons", "hr_server.py")
        return MultiServerMCPClient(
            {
                "workstack_hr": {
                    "command": "python",
                    "args": [server_path, "--transport", "stdio"],
                    "transport": "stdio",
                }
            }
        )

    url = getattr(
        settings,
        "MCP_SSE_URL",
        os.environ.get("MCP_SSE_URL", "http://workstack_mcp_hr:8080/sse"),
    )
    return MultiServerMCPClient(
        {
            "workstack_hr": {
                "url": url,
                "transport": "sse",
            }
        }
    )
