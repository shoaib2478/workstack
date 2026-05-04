from rest_framework import viewsets, mixins
from rest_framework.permissions import IsAuthenticated
from .models import Role, Permission
from .serializers import RoleSerializer, PermissionSerializer
from core.permissions import HasOrganizationPermission
from apps.organizations.models import Organization

class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Global list of all available permissions in the system.
    Read-only for everyone.
    """
    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    permission_classes = [IsAuthenticated]
    # No pagination needed usually, but good to have if list gets massive

class RoleViewSet(viewsets.ModelViewSet):
    
    serializer_class = RoleSerializer
    
    # Require them to be logged in AND have the specific RBAC permission to view/edit roles
    permission_classes = [
        IsAuthenticated, 
        HasOrganizationPermission('roles:read')  # We'll rely on ViewSet action to tighten this later
    ]

    def get_queryset(self):
        # 1. Extract the active organization from the header
        org_uuid = self.request.META.get('HTTP_X_ORGANIZATION_ID')
        # add org mixin later
        organization = Organization.objects.get(uuid=org_uuid)
        # 2. Security Check: Ensure the user actually belongs to this org!
        user_belongs_to_org = self.request.user.memberships.filter(
            organization=organization, 
            is_active=True
        ).exists()

        if not user_belongs_to_org:
            return Role.objects.none() # Return empty if they are hacking the header

        # 3. Return only roles for this specific tenant
        return Role.objects.filter(organization=organization)