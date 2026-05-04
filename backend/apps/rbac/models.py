from enum import unique
from re import L
from django.db import models
from core.models import BaseTimeStampedModel
from apps.users.models import User

class Permission(BaseTimeStampedModel):
    code = models.CharField(max_length = 128, unique = True, help_text = "'payroll:read', 'employee:write', 'org_chart:delete'")
    description = models.TextField(blank=True)

class Role(BaseTimeStampedModel):
    organization = models.ForeignKey(
        'organizations.Organization', 
        on_delete=models.CASCADE, 
        related_name='roles'
    )
    name = models.CharField(max_length=100, help_text = "Acme Corp can have a custom SuperManager role")
    description = models.TextField(blank=True)
    last_modified_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="modified_roles")

    class Meta:
        # Prevent duplicate role names within the same company
        constraints = [
            models.UniqueConstraint(fields=['organization', 'name'], name='unique_org_role_name')
        ]

    def __str__(self):
        return f"{self.name} ({self.organization.name})"

class RolePermission(BaseTimeStampedModel):
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)
    
    granted_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="granted_rolepermissions")
    last_modified_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL, related_name="modified_rolepermissions"
    )

class MemberRole(BaseTimeStampedModel):
    member = models.ForeignKey(
        'organizations.OrganizationMember', 
        on_delete=models.CASCADE, 
        related_name='role_assignments'
    )
    role = models.ForeignKey(
        Role, 
        on_delete=models.CASCADE, 
        related_name='member_assignments'
    )

    granted_by = models.ForeignKey(
        User, 
        null=True, 
        on_delete=models.SET_NULL, 
        related_name="granted_memberroles"
    )

    last_modified_by = models.ForeignKey(
        User, 
        null=True, 
        on_delete=models.SET_NULL, 
        related_name="modified_memberroles"
    )
    
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['member', 'role'], name='unique_member_role')
        ]

    def __str__(self):
        return f"{self.member} is {self.role.name}"
    