from rest_framework import serializers
from .models import Employee

class EmployeeSerializer(serializers.ModelSerializer):
    """
    Serializes Employee nodes safely, exposing UUIDs instead of internal IDs,
    and flattening related User/Department data for the frontend.
    """
    # 1. Traverse the relationships securely
    uuid = serializers.UUIDField(source='user.uuid', read_only=True)
    name = serializers.CharField(source='user.get_full_name', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True, allow_null=True)
    
    # 2. Compute dynamic fields for the UI
    has_children = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = [
            'uuid',
            'email', 
            'name', 
            'job_title', 
            'department_name', 
            'is_active', 
            'has_children'
        ]

    def get_has_children(self, obj):
        # Read directly from Treebeard's optimized numchild column
        return obj.numchild > 0