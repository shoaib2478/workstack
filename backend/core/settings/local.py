from .base import *

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env('DJANGO_SECRET_KEY', default='django-insecure-local-dev-key')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['localhost', '127.0.0.1', '0.0.0.0']

# INSTALLED_APPS += ['debug_toolbar']

# If using ngrok to test webhooks, add the ngrok url here
CSRF_TRUSTED_ORIGINS = [
    'https://*.ngrok-free.app',
    'http://localhost',
    'http://127.0.0.1',
]
