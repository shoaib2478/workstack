from rest_framework import serializers

class InviteUserSerializer(serializers.Serializer):
    email = serializers.EmailField()
    # role_id is optional. If not provided, our service falls back to the default role!
    role_uuid = serializers.UUIDField(required=False, allow_null = True)
    job_title = serializers.CharField(max_length=255, default="New Hire")
    department_uuid = serializers.IntegerField(required=False, allow_null=True)
    manager_uuid = serializers.UUIDField(required=False, allow_null=True) # Who do they report to?

class AcceptInviteSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
    