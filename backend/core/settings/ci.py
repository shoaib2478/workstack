"""
CI settings — used by GitHub Actions only.

Differences from local.py:
  - Reads credentials from environment variables (set in the workflow file)
  - Uses Redis as Celery broker (RabbitMQ is not started in CI)
  - PASSWORD_HASHERS uses MD5 so unit tests run faster
  - EMAIL_BACKEND is in-memory (no SMTP needed)
  - All external service calls (Gemini, MCP daemons) are auto-skipped
    because the integration tests check reachability before running.
"""
from .base import *  # noqa: F401, F403

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Use a fast hasher so test user creation is not the bottleneck
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# No email sends during CI
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
