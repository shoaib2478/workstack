from rest_framework import serializers
from .models import Role, Permission

class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ['code', 'description']

class RoleSerializer(serializers.ModelSerializer):   
    # Instead of nested JSON objects, React just wants: permissions: ["users:read", "payroll:write"]
    permissions = serializers.SerializerMethodField()
    organization = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ['uuid', 'organization', 'name', 'description', 'permissions', 'created_at', 'updated_at']

    def get_permissions(self, obj):        
        return obj.rolepermission_set.values_list('permission__code', flat=True)

    def get_organization(self, obj):
        return obj.organization.uuid