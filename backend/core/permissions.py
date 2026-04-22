from rest_framework import permissions
from apps.rbac.services import RBACService
import structlog

logger = structlog.get_logger(__name__)

class HasOrganizationPermission(permissions.BasePermission):
    """
    Custom DRF Permission class that hooks into our high-speed Redis RBACService.
    Usage in ViewSet: permission_classes = [IsAuthenticated, HasOrganizationPermission('payroll:write')]
    """
    def __init__(self, required_permission_code: str):
        self.required_permission_code = required_permission_code

    def __call__(self):
        # This allows us to pass arguments to the permission class in the ViewSet
        return self

    def has_permission(self, request, view):
        
        log = logger.bind(endpoint=request.path)
            
        user_id = request.user.id
        log.bind(user_id=user_id)
        org_id_header = request.META.get('HTTP_X_ORGANIZATION_ID')
        if not org_id_header:
            # log.warning(status="missing_org_header")
            return False

        try:
            organization_id = int(org_id_header)
        except ValueError:
            return False
        log.bind(organization_id=organization_id)
        has_access = RBACService.has_permission(
            user_id=user_id, 
            organization_id=organization_id, 
            permission_code=self.required_permission_code
        )

        if not has_access:
            log.warning(
                status="rbac_denied",                
                attempted_action=self.required_permission_code
            )

        return has_access