"""
RBAC + SimpleJWT authorization tests.

Matches Postman collection: RBAC.postman_collection.json
  GET /api/v1/rbac/permissions/
  GET /api/v1/rbac/roles/  (+ X-Organization-ID header)

Also covers RBACService cache-aside logic used by HasOrganizationPermission.

Run:
    python manage.py test apps.rbac.tests.test_rbac_api -v 2
"""
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.rbac.services import RBACService
from core.tests.helpers import org_header, signup_tenant


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class RBACAPITest(APITestCase):
    PERMISSIONS_URL = "/api/v1/rbac/permissions/"
    ROLES_URL = "/api/v1/rbac/roles/"

    def setUp(self):
        cache.clear()

    def test_permissions_list_requires_authentication(self):
        response = self.client.get(self.PERMISSIONS_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_can_list_global_permissions(self):
        """GET /permissions/ — global taxonomy, any logged-in user (Postman: permissions)."""
        signup_tenant(self.client, email="rbac@acmecorp.com")

        response = self.client.get(self.PERMISSIONS_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        codes = {item["code"] for item in response.data}
        self.assertIn("users:read", codes)
        self.assertIn("roles:read", codes)
        self.assertIn("org:write", codes)

    def test_super_admin_can_list_org_roles_with_header(self):
        """GET /roles/ + X-Organization-ID (Postman: get_roles)."""
        _admin, org = signup_tenant(self.client, email="admin-rbac@acmecorp.com")

        response = self.client.get(self.ROLES_URL, **org_header(org))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        role_names = {role["name"] for role in response.data}
        self.assertIn("Super Admin", role_names)
        self.assertIn("Standard Employee", role_names)

        super_admin = next(r for r in response.data if r["name"] == "Super Admin")
        self.assertIn("users:write", super_admin["permissions"])
        self.assertIn("roles:read", super_admin["permissions"])

    def test_roles_list_denied_without_org_header(self):
        """HasOrganizationPermission returns False when X-Organization-ID is missing."""
        signup_tenant(self.client, email="noheader@acmecorp.com")

        response = self.client.get(self.ROLES_URL)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class RBACServiceTest(APITestCase):
    """Unit tests for RBACService — the layer behind HasOrganizationPermission."""

    def setUp(self):
        cache.clear()

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_super_admin_has_users_write_permission(self):
        admin, org = signup_tenant(self.client, email="svc-admin@acmecorp.com")

        allowed = RBACService.has_permission(
            user_id=admin.id,
            organization_id=org.id,
            permission_code="users:write",
        )
        self.assertTrue(allowed)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_standard_employee_lacks_roles_write(self):
        _admin, org = signup_tenant(self.client, email="svc-inviter@acmecorp.com")

        invite_response = self.client.post(
            "/api/v1/organizations/invites/",
            {"email": "employee@newhire.com"},
            format="json",
            **org_header(org),
        )
        self.assertEqual(invite_response.status_code, status.HTTP_201_CREATED)

        from core.tests.helpers import extract_invite_token
        from rest_framework.test import APIClient

        token = extract_invite_token(invite_response.data["debug_invite_link"])
        employee_client = APIClient()
        employee_client.post(
            "/api/v1/organizations/invites/accept/",
            {"token": token, "password": "MyNewPassword123"},
            format="json",
        )

        employee = org.members.get(user__email="employee@newhire.com").user
        allowed = RBACService.has_permission(
            user_id=employee.id,
            organization_id=org.id,
            permission_code="roles:write",
        )
        self.assertFalse(allowed)
