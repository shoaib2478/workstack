from rest_framework import permissions
from apps.rbac.services import RBACService
import structlog
import uuid

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
        
        log = logger.bind(event_type='has_permission')
            
        user_id = request.user.id
        log.bind(user_id=user_id)
        org_id_header = request.META.get('HTTP_X_ORGANIZATION_ID')
        if not org_id_header:
            # log.warning(status="missing_org_header")
            return False

        try:
            # validate uuid
            organization_uuid = uuid.UUID(org_id_header)
            from apps.organizations.models import Organization
            organization = Organization.objects.get(uuid=organization_uuid)
            organization_id = organization.id
        except ValueError as excp:
            log.error("has_permission_failed", status="rbac_ValueError", excp=excp)
            return False
        except TypeError as excp:
            log.error("has_permission_failed", status="rbac_TypeError", excp=excp)
            return False
        except Exception as excp:
            log.error("has_permission_failed", status="rbac_Exception", excp=excp)
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