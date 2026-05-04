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
from core.utils.auth import set_jwt_cookies

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
            # 2. Get the actual User object           
            user = User.objects.get(username=request.data['username'])
            
            # 3. Apply our secure cookies
            response = set_jwt_cookies(response, user)
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

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from apps.organizations.services import TenantRegistrationService
from .serializers import SignupSerializer

class SignupView(APIView):
    """
    Public endpoint to register a new company and admin user.
    Automatically generates and attaches HttpOnly JWT cookies upon success.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        if serializer.is_valid():
            try:
                # 1. Call our atomic service layer
                user = TenantRegistrationService.provision_new_tenant(
                    email=serializer.validated_data['email'],
                    password=serializer.validated_data['password'],
                    first_name=serializer.validated_data['first_name'],
                    last_name=serializer.validated_data['last_name'],
                    company_name=serializer.validated_data['company_name']
                )

                # 2. Generate the JWT tokens for the newly created user
                response = Response(
                    {"message": "Registration successful. Welcome to Workstack!"}, 
                    status=status.HTTP_201_CREATED
                )
                return set_jwt_cookies(response, user)
                
            except Exception as e:
                logger.error("tenant_provisioning_failed", error=str(e))
                return Response(
                    {"error": str(e)}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
                
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# class PayrollViewSet(viewsets.ModelViewSet):
    # DRF checks IsAuthenticated FIRST. If true, it checks our custom RBAC SECOND.
    # permission_classes = [IsAuthenticated, HasOrganizationPermission('payroll:write')]

