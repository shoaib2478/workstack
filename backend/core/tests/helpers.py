"""
Shared helpers for API tests — copy this pattern for hris and other apps.

Pattern:
  1. signup_tenant()  → POST /auth/signup/ (provisions org + admin + cookies)
  2. login_user()     → POST /auth/login/  (sets HttpOnly JWT cookies on client)
  3. org_header()     → HTTP_X_ORGANIZATION_ID kwarg for RBAC-protected endpoints
"""
from __future__ import annotations

from apps.organizations.models import Organization
from apps.users.models import User
from rest_framework.test import APIClient


def signup_payload(
    *,
    email: str = "admin@acmecorp.com",
    password: str = "SuperSecretPassword123!",
    first_name: str = "Shuaib",
    last_name: str = "Sayyad",
    company_name: str = "Acme Corp",
) -> dict:
    return {
        "email": email,
        "password": password,
        "first_name": first_name,
        "last_name": last_name,
        "company_name": company_name,
    }


def signup_tenant(
    client: APIClient,
    *,
    email: str = "admin@acmecorp.com",
    password: str = "SuperSecretPassword123!",
    first_name: str = "Shuaib",
    last_name: str = "Sayyad",
    company_name: str = "Acme Corp",
) -> tuple[User, Organization]:
    """Register a new tenant; JWT cookies are stored on `client`."""
    response = client.post(
        "/api/v1/auth/signup/",
        signup_payload(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            company_name=company_name,
        ),
        format="json",
    )
    assert response.status_code == 201, response.data

    user = User.objects.get(email=email)
    org = user.memberships.get(is_active=True).organization
    return user, org


def login_user(
    client: APIClient,
    *,
    username: str,
    password: str,
) -> None:
    """Login via cookie JWT flow; tokens are NOT returned in JSON body."""
    response = client.post(
        "/api/v1/auth/login/",
        {"username": username, "password": password},
        format="json",
    )
    assert response.status_code == 200, response.data


def org_header(organization: Organization) -> dict:
    return {"HTTP_X_ORGANIZATION_ID": str(organization.uuid)}


def extract_invite_token(debug_invite_link: str) -> str:
    return debug_invite_link.split("token=", 1)[1]
