import os
import sys
import django
from mcp.server.fastmcp import FastMCP


# 1. Setup Django environment inside the subprocess
# Adjust 'config.settings' to match your actual DJANGO_SETTINGS_MODULE
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../..")
)
sys.path.append(PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local")
django.setup()

# Now we can safely import Django models
from apps.hris.models import Employee

# 2. Initialize FastMCP
mcp = FastMCP("Workstack_Org_Chart")

@mcp.tool()
def get_employee_manager(email: str) -> str:
    """Finds the manager's name and email for a given employee email."""
    try:
        employee = Employee.objects.select_related('manager').get(email=email.lower())
        print("employee >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>", employee)
        if employee.manager:
            return f"Manager: {employee.manager.first_name} {employee.manager.last_name} ({employee.manager.email})"
        return "This employee has no assigned manager (likely top-level tier)."
    except Employee.DoesNotExist:
        return f"Error: No employee found with email {email}."

if __name__ == "__main__":
    mcp.run()