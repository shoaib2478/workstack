from rest_framework import serializers
from .models import User

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

