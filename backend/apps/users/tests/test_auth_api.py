"""
Auth API tests — signup, login (HttpOnly JWT cookies), logout.

Matches Postman collection: User.postman_collection.json
  POST /api/v1/auth/signup/
  POST /api/v1/auth/login/
  POST /api/v1/auth/logout/

Run:
    python manage.py test apps.users.tests.test_auth_api -v 2
"""
from django.conf import settings
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.organizations.models import Organization, OrganizationMember
from apps.users.models import User
from core.tests.helpers import signup_tenant


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class AuthAPITest(APITestCase):
    SIGNUP_URL = "/api/v1/auth/signup/"
    LOGIN_URL = "/api/v1/auth/login/"
    LOGOUT_URL = "/api/v1/auth/logout/"

    def test_signup_provisions_tenant_and_sets_jwt_cookies(self):
        """
        python manage.py test apps.users.tests.test_auth_api.AuthAPITest.test_signup_provisions_tenant_and_sets_jwt_cookies -v 2
        Expected:
        - Status code: 201
        - Message: "Registration successful. Welcome to Workstack!"
        - Tokens must NOT leak in JSON — they live in HttpOnly cookies only
        - Cookies:
          - access_token
          - refresh_token
        """
        """Public signup → org + admin user + HttpOnly cookies (Postman: signup)."""
        response = self.client.post(
            self.SIGNUP_URL,
            {
                "email": "founder@acmecorp.com",
                "password": "SuperSecretPassword123!",
                "first_name": "Shuaib",
                "last_name": "Sayyad",
                "company_name": "Acme Corp",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["message"], "Registration successful. Welcome to Workstack!")
        # Tokens must NOT leak in JSON — they live in HttpOnly cookies only
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)

        cookie_names = settings.SIMPLE_JWT
        self.assertIn(cookie_names["AUTH_COOKIE"], response.cookies)
        self.assertIn(cookie_names["AUTH_COOKIE_REFRESH"], response.cookies)

        user = User.objects.get(email="founder@acmecorp.com")
        self.assertTrue(
            OrganizationMember.objects.filter(user=user, is_active=True).exists()
        )
        self.assertTrue(Organization.objects.filter(domain="acmecorp.com").exists())

    def test_login_sets_cookies_and_hides_tokens_from_body(self):
        """Login returns message only; JWTs are in cookies (Postman: login_admin_user)."""
        signup_tenant(
            self.client,
            email="shuaib@acmecorp.com",
            password="SuperSecretPassword123!",
        )
        self.client.cookies.clear()

        response = self.client.post(
            self.LOGIN_URL,
            {"username": "shuaib@acmecorp.com", "password": "SuperSecretPassword123!"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "Login successful")
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)
        self.assertIn(settings.SIMPLE_JWT["AUTH_COOKIE"], response.cookies)

    def test_logout_clears_jwt_cookies(self):
        """Logout deletes access + refresh cookies (Postman pattern)."""
        signup_tenant(self.client, email="logout@acmecorp.com")

        response = self.client.post(self.LOGOUT_URL, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "Logout successful")

        access_cookie = response.cookies.get(settings.SIMPLE_JWT["AUTH_COOKIE"])
        refresh_cookie = response.cookies.get(settings.SIMPLE_JWT["AUTH_COOKIE_REFRESH"])
        self.assertIsNotNone(access_cookie)
        self.assertEqual(access_cookie.value, "")
        self.assertIsNotNone(refresh_cookie)
        self.assertEqual(refresh_cookie.value, "")

    def test_protected_users_endpoint_requires_login(self):
        """Unauthenticated GET /users/ → 401."""
        response = self.client.get("/api/v1/users/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_can_list_org_members(self):
        """Cookie JWT auth works end-to-end on a protected endpoint."""
        user, _org = signup_tenant(self.client, email="list@acmecorp.com")

        response = self.client.get("/api/v1/users/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        emails = [entry["email"] for entry in response.data]
        self.assertIn(user.email, emails)
