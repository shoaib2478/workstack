import os
import sys
import django
from asgiref.sync import sync_to_async
from mcp.server.fastmcp import FastMCP

# 1. Boot Django exactly ONCE at startup
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local")
django.setup()

from django.contrib.auth import get_user_model
from apps.hris.models import Employee 

mcp = FastMCP("Workstack_HR_Daemon", host="0.0.0.0", port=8080)

@mcp.tool()
async def get_employee_manager(email: str) -> str:
    """Fetch the manager for an employee. Pass the employee's email address as the parameter."""
    
    # We wrap the synchronous Django ORM logic inside this helper
    @sync_to_async
    def _query_db():
        User = get_user_model()
        try:
            # 1. Get the User
            user = User.objects.get(username=email)
            
            # 2. Get the Employee (Assuming Employee has a OneToOne to User)
            # We select_related on 'manager' and the manager's 'user' to prevent N+1 queries
            employee = Employee.objects.get(user=user)
            manager = employee.get_parent()
            if not manager:
                return "This employee has no assigned manager."
            
            if manager:
                return f"Manager: {manager.user.first_name} {manager.user.last_name} ({manager.user.username})"
            return "This employee has no assigned manager."
            
        except User.DoesNotExist:
            return f"Error: No user found with username/email {email}."
        except Employee.DoesNotExist:
            return f"Error: No employee found with username/email {email}."
        except Exception as e:
            return f"Error: {str(e)}"

    # Execute the wrapped function asynchronously
    return await _query_db()

if __name__ == "__main__":
    print("Starting MCP SSE Daemon on port 8080...")
    mcp.run(transport="sse")