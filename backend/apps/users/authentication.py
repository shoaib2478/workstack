from rest_framework_simplejwt.authentication import JWTAuthentication
from django.conf import settings

class CustomCookieJWTAuthentication(JWTAuthentication):
    """
    Tells DRF to look for the JWT inside the HttpOnly cookie 
    instead of the Authorization header.
    """

    def authenticate(self, request):
        header = self.get_header(request)

        # If the header exists, someone is using Postman/cURL with a Bearer token. Let it pass.
        if header is not None:
            raw_token = self.get_raw_toekn()
        else:
            # Otherwise, extract the token from the HttpOnly cookie (The React Flow)
            raw_token = request.COOKIES.get(settings.SIMPLE_JWT['AUTH_COOKIE'])

        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)

        return self.get_user(validated_token), validated_token

