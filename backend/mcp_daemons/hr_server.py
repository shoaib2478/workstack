import argparse
import os
import sys
import django
from asgiref.sync import sync_to_async
from mcp.server.fastmcp import FastMCP

# Boot Django exactly ONCE at startup
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local")
django.setup()

from django.contrib.auth import get_user_model
from apps.hris.models import Employee


def _register_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_employee_manager(email: str) -> str:
        """Fetch the manager for an employee. Pass the employee's email address as the parameter."""

        @sync_to_async
        def _query_db():
            User = get_user_model()
            try:
                user = User.objects.get(username=email)
                employee = Employee.objects.get(user=user)
                manager = employee.get_parent()
                if not manager:
                    return "This employee has no assigned manager."
                return (
                    f"Manager: {manager.user.first_name} {manager.user.last_name} "
                    f"({manager.user.username})"
                )
            except User.DoesNotExist:
                return f"Error: No user found with username/email {email}."
            except Employee.DoesNotExist:
                return f"Error: No employee found with username/email {email}."
            except Exception as e:
                return f"Error: {str(e)}"

        return await _query_db()


def run_stdio():
    """Subprocess mode for Celery/LangGraph — JSON-RPC on stdin/stdout only."""
    mcp = FastMCP("Workstack_HR_Daemon")
    _register_tools(mcp)
    mcp.run()


def run_sse():
    """Persistent daemon mode for Docker — HTTP/SSE on port 8080."""
    mcp = FastMCP("Workstack_HR_Daemon", host="0.0.0.0", port=8080)
    _register_tools(mcp)
    print("Starting MCP SSE Daemon on port 8080...", file=sys.stderr)
    mcp.run(transport="sse")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Workstack HR MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="sse",
        help="stdio for subprocess clients; sse for persistent daemon (default: sse)",
    )
    args = parser.parse_args()
    if args.transport == "stdio":
        run_stdio()
    else:
        run_sse()
