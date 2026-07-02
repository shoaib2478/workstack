"""
Organization onboarding + invite API tests.

Matches Postman collections:
  User.postman_collection.json        → signup (tenant provisioning)
  organization.postman_collection.json → invite_user, accept_invited_user

Run:
    python manage.py test apps.organizations.tests.test_onboarding_invite_api -v 2
"""
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.organizations.models import OrganizationMember
from apps.users.models import User
from core.tests.helpers import (
    extract_invite_token,
    org_header,
    signup_tenant,
)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class OnboardingInviteAPITest(APITestCase):
    INVITE_URL = "/api/v1/organizations/invites/"
    ACCEPT_URL = "/api/v1/organizations/invites/accept/"

    def test_signup_onboards_tenant_with_super_admin_role(self):
        """TenantRegistrationService: user + org + default RBAC roles + CEO node."""
        _user, org = signup_tenant(
            self.client,
            email="onboard@acmecorp.com",
            company_name="Acme Corp",
        )

        self.assertEqual(org.name, "Acme Corp")
        self.assertEqual(org.domain, "acmecorp.com")

        admin_role = org.roles.get(name="Super Admin")
        self.assertTrue(
            admin_role.rolepermission_set.filter(
                permission__code="users:write"
            ).exists()
        )

    def test_admin_can_invite_user(self):
        """POST /invites/ with users:write + X-Organization-ID (Postman: invite_user)."""
        _admin, org = signup_tenant(self.client, email="admin@acmecorp.com")

        response = self.client.post(
            self.INVITE_URL,
            {"email": "katrina@newtech.com"},
            format="json",
            **org_header(org),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("Invite successfully generated", response.data["message"])
        self.assertIn("debug_invite_link", response.data)

        invited = User.objects.get(email="katrina@newtech.com")
        membership = OrganizationMember.objects.get(user=invited, organization=org)
        self.assertFalse(membership.is_active)

    def test_invited_user_can_accept_and_receive_jwt_cookies(self):
        """POST /invites/accept/ activates membership + sets cookies (Postman: accept)."""
        admin, org = signup_tenant(self.client, email="inviter@acmecorp.com")

        invite_response = self.client.post(
            self.INVITE_URL,
            {"email": "katrina@newhire.com", "manager_uuid": str(admin.uuid)},
            format="json",
            **org_header(org),
        )
        self.assertEqual(invite_response.status_code, status.HTTP_201_CREATED)

        token = extract_invite_token(invite_response.data["debug_invite_link"])

        accept_client = APIClient()
        accept_response = accept_client.post(
            self.ACCEPT_URL,
            {"token": token, "password": "MyNewPassword123"},
            format="json",
        )

        self.assertEqual(accept_response.status_code, status.HTTP_200_OK)
        self.assertEqual(accept_response.data["message"], "Welcome to Workstack!")

        invited = User.objects.get(email="katrina@newhire.com")
        membership = OrganizationMember.objects.get(user=invited, organization=org)
        self.assertTrue(membership.is_active)
        self.assertTrue(invited.check_password("MyNewPassword123"))
        self.assertIn("access_token", accept_response.cookies)

    def test_invite_requires_users_write_permission(self):
        """Missing X-Organization-ID → RBAC denies invite."""
        signup_tenant(self.client, email="norole@acmecorp.com")

        response = self.client.post(
            self.INVITE_URL,
            {"email": "blocked@newtech.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
