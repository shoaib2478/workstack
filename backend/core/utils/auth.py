from django.conf import settings
from rest_framework_simplejwt.tokens import RefreshToken

def set_jwt_cookies(response, user):
    """
    Utility to generate JWTs and securely attach them to an HTTP Response as HttpOnly cookies.
    """
    refresh = RefreshToken.for_user(user)
    
    response.set_cookie(
        key=settings.SIMPLE_JWT['AUTH_COOKIE'],
        value=str(refresh.access_token),
        expires=settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'],
        secure=settings.SIMPLE_JWT['AUTH_COOKIE_SECURE'],
        httponly=settings.SIMPLE_JWT['AUTH_COOKIE_HTTP_ONLY'],
        samesite=settings.SIMPLE_JWT['AUTH_COOKIE_SAMESITE']
    )
    response.set_cookie(
        key=settings.SIMPLE_JWT['AUTH_COOKIE_REFRESH'],
        value=str(refresh),
        expires=settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'],
        secure=settings.SIMPLE_JWT['AUTH_COOKIE_SECURE'],
        httponly=settings.SIMPLE_JWT['AUTH_COOKIE_HTTP_ONLY'],
        samesite=settings.SIMPLE_JWT['AUTH_COOKIE_SAMESITE']
    )
    return response