from django.db import models
from django.conf import settings
from core.models import BaseTimeStampedModel

class Organization(BaseTimeStampedModel):
    name = models.CharField(
        max_length=255,
        help_text="The public display name of the organization (e.g., 'Acme Corp')."
    )
    slug = models.SlugField(
        max_length=255, 
        unique=True,
        help_text="A unique, URL-friendly identifier for API routing or subdomains (e.g., 'acme-corp')."
    )
    domain = models.CharField(
        max_length=255, 
        blank=True, 
        null=True,
        help_text="The primary email domain for user auto-discovery (e.g., 'acme.com')."
    )

    def __str__(self):
        return self.name

class OrganizationMember(BaseTimeStampedModel):
    organization = models.ForeignKey(
        Organization, 
        on_delete=models.CASCADE, 
        related_name='members',
        help_text="The organization this membership is tied to."
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='memberships',
        help_text="The global user holding this specific membership."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Designates whether the user currently has access to this organization's data."
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'user'], 
                name='unique_org_user_membership'
            )
        ]

    def __str__(self):
        status = "Active" if self.is_active else "Inactive"
        return f"[{status}] {self.user} at {self.organization.name}"