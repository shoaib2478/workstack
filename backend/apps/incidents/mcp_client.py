import os

from django.conf import settings
from langchain_mcp_adapters.client import MultiServerMCPClient


def build_mcp_client() -> MultiServerMCPClient:
    """
    Build MCP client connecting to all Workstack tool servers.

    Servers:
        workstack_hr     — get_employee_manager (hr_server.py :8080)
        workstack_triage — read_triage_chunk, list_triage_references (triage_server.py :8090)

    Transport:
        MCP_TRANSPORT=sse  (default) → HTTP/SSE to persistent daemons
        MCP_TRANSPORT=stdio          → subprocess spawn (local dev without Docker daemons)
    """
    transport = getattr(settings, "MCP_TRANSPORT", "sse")

    if transport == "stdio":
        base = settings.BASE_DIR
        return MultiServerMCPClient(
            {
                "workstack_hr": {
                    "command": "python",
                    "args": [
                        os.path.join(base, "mcp_daemons", "hr_server.py"),
                        "--transport", "stdio",
                    ],
                    "transport": "stdio",
                },
                "workstack_triage": {
                    "command": "python",
                    "args": [
                        os.path.join(base, "mcp_daemons", "triage_server.py"),
                        "--transport", "stdio",
                    ],
                    "transport": "stdio",
                },
            }
        )

    hr_url = getattr(
        settings,
        "MCP_SSE_URL",
        os.environ.get("MCP_SSE_URL", "http://workstack_mcp_hr:8080/sse"),
    )
    triage_url = getattr(
        settings,
        "MCP_TRIAGE_SSE_URL",
        os.environ.get("MCP_TRIAGE_SSE_URL", "http://workstack_mcp_triage:8090/sse"),
    )
    return MultiServerMCPClient(
        {
            "workstack_hr": {
                "url": hr_url,
                "transport": "sse",
            },
            "workstack_triage": {
                "url": triage_url,
                "transport": "sse",
            },
        }
    )
