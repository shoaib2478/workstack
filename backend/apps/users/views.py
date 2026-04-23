from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from apps.users.models import User
from .serializers import UserSerializer, CustomTokenObtainPairSerializer
from core.permissions import HasOrganizationPermission
from django.conf import settings
import structlog

logger = structlog.get_logger("workstack")

class CookieTokenObtainPairView(TokenObtainPairView):
    """
    Intercepts the standard login flow to place the JWTs into HttpOnly cookies
    and removes them from the JSON body to prevent XSS theft.
    """
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        # 1. Let SimpleJWT generate the tokens normally
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            # 2. Extract the tokens from the JSON response
            access_token = response.data.get('access')
            refresh_token = response.data.get('refresh')

            # 3. Set the HttpOnly Cookies
            response.set_cookie(
                key=settings.SIMPLE_JWT['AUTH_COOKIE'],
                value=access_token,
                expires=settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'],
                secure=settings.SIMPLE_JWT['AUTH_COOKIE_SECURE'],
                httponly=settings.SIMPLE_JWT['AUTH_COOKIE_HTTP_ONLY'],
                samesite=settings.SIMPLE_JWT['AUTH_COOKIE_SAMESITE']
            )
            response.set_cookie(
                key=settings.SIMPLE_JWT['AUTH_COOKIE_REFRESH'],
                value=refresh_token,
                expires=settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'],
                secure=settings.SIMPLE_JWT['AUTH_COOKIE_SECURE'],
                httponly=settings.SIMPLE_JWT['AUTH_COOKIE_HTTP_ONLY'],
                samesite=settings.SIMPLE_JWT['AUTH_COOKIE_SAMESITE']
            )
            # 4. Remove the raw tokens from the JSON body [Security]
            del response.data['access']
            del response.data['refresh']
            
            # Optional: Return basic user info so React knows who just logged in
            response.data['message'] = "Login successful"
            
            logger.info("user_login_success", endpoint=request.path)

        return response

class LogoutView(APIView):
    """
    Logs the user out by deleting the HttpOnly cookies.
    """
    def post(self, request):
        response = Response({"message": "Logout successful"}, status=status.HTTP_200_OK)
        
        # Overwrite the cookies with empty values that expire immediately
        response.delete_cookie(settings.SIMPLE_JWT['AUTH_COOKIE'])
        response.delete_cookie(settings.SIMPLE_JWT['AUTH_COOKIE_REFRESH'])
        
        logger.info("user_logout_success", user_id=request.user.id)
        return response

class UserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.filter(is_active=True)
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    permission_classes = [IsAuthenticated, HasOrganizationPermission('payroll:write')]
    lookup_field = 'uuid'



# class PayrollViewSet(viewsets.ModelViewSet):
    # DRF checks IsAuthenticated FIRST. If true, it checks our custom RBAC SECOND.
    # permission_classes = [IsAuthenticated, HasOrganizationPermission('payroll:write')]

