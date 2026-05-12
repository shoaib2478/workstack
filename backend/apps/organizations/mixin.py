from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.organizations.models import Organization

class OrganizationMixin:
    """
    Extracts the Organization UUID from the header and securely verifies 
    that the current user is an active member of it.
    """
    @property
    def organization(self):
        if hasattr(self, '_organization'):
            return self._organization

        org_uuid = self.request.META.get('HTTP_X_ORGANIZATION_ID')
        if not org_uuid:
            raise ValidationError({"detail" : "HTTP_X_ORGANIZATION_ID header is missing."})

        membership = self.request.user.memberships.filter(organization__uuid=org_uuid).select_related('organization').first()
        if not membership:
            raise PermissionDenied("You do not have active access to this organization.")
        
        self._organization = membership.organization
        return self._organization

