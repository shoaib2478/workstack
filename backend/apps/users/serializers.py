from rest_framework import serializers
from .models import User

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework.exceptions import AuthenticationFailed

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        # 1. Run the standard username/password validation
        data = super().validate(attrs)
        
        # 2. Block Superusers/Staff
        if self.user.is_superuser or self.user.is_staff:
            raise AuthenticationFailed(
                "Admin accounts cannot log into the tenant API. Use the Django Admin panel."
            )
            
        return data

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User

        # Explicitly define fields. NEVER use '__all__' in production [Security].
        fields = [
            'uuid', 
            'email', 
            'username', 
            'first_name', 
            'last_name', 
            'is_active',
            'date_joined'
        ]


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    company_name = serializers.CharField(max_length=255)