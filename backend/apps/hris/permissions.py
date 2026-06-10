from rest_framework.permissions import BasePermission
from .models import Employee
import structlog

logger = structlog.get_logger("workstack")

class IsManagerOfEmployee(BasePermission):
    """
    [ReBAC]: Grants access only if the logged-in user is an ancestor 
    (Manager, VP, CEO) of the target Employee node in the Org Chart.
    """
    message = "You do not have manager permissions for this employee."

    def has_permission(self, request, view):
        # We only care about object-level permissions here.
        # Let the standard IsAuthenticated handle the general view access.
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        """
        `obj` is the target Employee the user is trying to access/edit.
        """
        # 1. Super Admins bypass this check (Using the RBAC service we built earlier)
        # Note: You can import your RBACService here to check for 'users:write'
        # if RBACService.has_permission(request.user.id, obj.organization_id, 'users:write'):
        #     return True

        # 2. Get the logged-in user's own Employee node
        try:
            requesting_employee = request.user.employee_profile
        except Employee.DoesNotExist:
            logger.warning("rebac_failed_no_profile", user_id=request.user.id)
            return False

        # 3. Prevent cross-tenant hacking attempts just in case
        if requesting_employee.organization_id != obj.organization_id:
            return False

        # 4. Employees can always view/edit their own profile
        if obj == requesting_employee:
            return True

        # 5. [THE SYSTEM DESIGN FLEX]
        # We do NOT run a recursive SQL query to check if the user is a manager.
        # Treebeard's `is_descendant_of()` method does pure string matching in memory.
        # It literally just runs: return obj.path.startswith(requesting_employee.path)
        # Time Complexity: O(1)
        is_manager = obj.is_descendant_of(requesting_employee)
        
        if not is_manager:
            logger.warning(
                "rebac_violation_attempt", 
                manager_id=requesting_employee.id, 
                target_employee_id=obj.id
            )

        return is_manager