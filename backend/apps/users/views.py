from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from apps.users.models import User
from .serializers import UserSerializer
from core.permissions import HasOrganizationPermission

class UserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.filter(is_active=True)
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    permission_classes = [IsAuthenticated, HasOrganizationPermission('payroll:write')]
    lookup_field = 'uuid'


# class PayrollViewSet(viewsets.ModelViewSet):
    # DRF checks IsAuthenticated FIRST. If true, it checks our custom RBAC SECOND.
    # permission_classes = [IsAuthenticated, HasOrganizationPermission('payroll:write')]

