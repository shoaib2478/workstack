import os
import sys
import django
from mcp.server.fastmcp import FastMCP

# 1. Boot Django exactly ONCE at startup
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local") # Adjust if using local.py
django.setup()

from apps.hris.models import Employee 

# 2. Initialize the Server
mcp = FastMCP("Workstack_HR_Daemon")

@mcp.tool()
def get_employee_manager(email: str) -> str:
    """Finds the manager's name and email for a given employee email."""
    try:
        employee = Employee.objects.select_related('manager').get(email=email.lower())
        if employee.manager:
            return f"Manager: {employee.manager.first_name} {employee.manager.last_name} ({employee.manager.email})"
        return "This employee has no assigned manager."
    except Employee.DoesNotExist:
        return f"Error: No employee found with email {email}."

if __name__ == "__main__":
    # 3. Run as a persistent SSE web server instead of a stdio subprocess!
    print("Starting MCP SSE Daemon on port 8080...")
    mcp.run(transport="sse", host="0.0.0.0", port=8080)