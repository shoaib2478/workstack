from rest_framework import serializers

class InviteUserSerializer(serializers.Serializer):
    email = serializers.EmailField()
    # role_id is optional. If not provided, our service falls back to the default role!
    role_uuid = serializers.UUIDField(required=False, allow_null = True)

class AcceptInviteSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)