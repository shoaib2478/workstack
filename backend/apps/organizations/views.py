from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.shortcuts import get_object_or_404
from apps.organizations.models import Organization, OrganizationMember
from apps.organizations.service.invites import InviteUserService
from .serializers import InviteUserSerializer, AcceptInviteSerializer
from core.permissions import HasOrganizationPermission
import structlog
import uuid
from apps.organizations.mixin import OrganizationMixin
from django.core.signing import TimestampSigner, SignatureExpired, BadSignature
from core.utils.auth import set_jwt_cookies


logger = structlog.get_logger("workstack")

class InviteUserView(APIView, OrganizationMixin):
    """
    Endpoint for Admins to invite new or existing users to their Organization.
    """
    permission_classes = [ IsAuthenticated, HasOrganizationPermission('users:write')]

    def post(self, request):
        log = logger.bind(event_type='InviteUserView:post')        
        serializer = InviteUserSerializer(data=request.data)
        
        if serializer.is_valid():
            organization_uuid = request.META.get('HTTP_X_ORGANIZATION_ID')
            organization_uuid = uuid.UUID(organization_uuid)
            
            
            try:
                organization = Organization.objects.get(uuid=organization_uuid)
                membership, accept_token = InviteUserService.invite_user(
                    caller=request.user,
                    organization=organization,
                    email=serializer.validated_data['email'],
                    role_uuid=serializer.validated_data.get('role_uuid')
                )
                return Response(
                    {
                        "message": f"Invite successfully generated for {serializer.validated_data['email']}.",
                        "debug_invite_link" : f"http://localhost:3000/accept-invite?token={accept_token}"
                    },
                    status=status.HTTP_201_CREATED
                )
            except ValueError as excp:
                log.error("failed", status="ValueError", excp=excp)
                return Response({"error" : str(excp)}, status=status.HTTP_400_BAD_REQUEST)
            except Exception as excp:
                log.error("failed", status="Exception", excp=excp)
                return Response({"error" : "Failed to process Invire."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AcceptInviteView(APIView):
    """
    Public endpoint for users to accept an invite, set their password, 
    activate their membership, and log in automatically.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        log = logger.bind(event_type="AcceptInviteView:post")
        serializer = AcceptInviteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        token = serializer.validated_data['token']
        password = serializer.validated_data['password']
        signer = TimestampSigner()

        try:
            payload = signer.unsign_object(token, max_age=172800)
            user_uuid = payload.get('user_id')
            org_uuid = payload.get('organization_id')
            membership_uuid = payload.get('membership_id')
            log = log.bind(user_uuid=user_uuid, org_uuid=org_uuid, membership_uuid=membership_uuid)
            membership = OrganizationMember.objects.select_related('user', 'organization').get(uuid=membership_uuid)
            if membership.is_active:
                return Response({"error": "This invite has already been accepted."}, status=status.HTTP_400_BAD_REQUEST)
            user = membership.user
            log = log.bind(user=user)
            user.set_password(password)
            user.save()

            membership.is_active = True
            membership.save()

            log.info("invite_accepted", user_id=user.id, org_id=membership.organization_id)

            # 6. Log them in seamlessly using our secure HttpOnly cookies!
            response = Response({"message": "Welcome to Workstack!"}, status=status.HTTP_200_OK)
            return set_jwt_cookies(response, user)
        except SignatureExpired:
            return Response({"error": "This invite link has expired. Please ask your admin for a new one."}, status=status.HTTP_400_BAD_REQUEST)
        except BadSignature:
            return Response({"error": "Invalid invite link. It may have been tampered with."}, status=status.HTTP_400_BAD_REQUEST)
        except OrganizationMember.DoesNotExist:
            return Response({"error": "Invite record not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as excp:
            log.error("accpet_invite_failed", status="accpet_invite_failed", excp=excp)
            return Response({"error" : "Failed to accept invited user."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR )
