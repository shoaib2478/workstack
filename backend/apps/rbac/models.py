from enum import unique
from re import L
from django.db import models
from core.models import BaseTimeStampedModel

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
    
    # [Database Design] ManyToManyField automatically creates the join table for us.
    permissions = models.ManyToManyField(Permission, related_name='roles', blank=True)

    class Meta:
        # Prevent duplicate role names within the same company
        constraints = [
            models.UniqueConstraint(fields=['organization', 'name'], name='unique_org_role_name')
        ]

    def __str__(self):
        return f"{self.name} ({self.organization.name})"

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

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['member', 'role'], name='unique_member_role')
        ]

    def __str__(self):
        return f"{self.member} is {self.role.name}"
    