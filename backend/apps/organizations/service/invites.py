from django.core.cache import cache
from apps.organizations.models import OrganizationMember, Organization, OrganizationSetting
from django.db import transaction
from apps.rbac.services import RBACService
from django.contrib.auth import get_user_model
from apps.rbac.models import Role, MemberRole
import structlog

from apps.users.models import User, UserSetting

import uuid
from django.core.signing import TimestampSigner

logger = structlog.get_logger("workstack")

class InviteUserService:
    """
    Handles inviting new or existing users into a specific Organization.
    """

    @classmethod
    @transaction.atomic
    def invite_user(cls, caller: User, organization: Organization, email: str, role_uuid: str = None, manager_uuid: str = None):
        
        log = logger.bind(event_type="invite_user", caller=caller, role_uuid=role_uuid, manager_uuid=manager_uuid)
        user, user_created = User.objects.get_or_create(
            email=email,
            defaults={'username': email}
        )
        log = log.bind(user=user, user_created=user_created)
        if user_created:
            # Prevent them from logging in with a password until they accept the invite
            user.set_unusable_password()
            user.save()
        _ = UserSetting.objects.get_or_create(user=user)
        # Prevent Duplicate Invites inside this specific Org
        if OrganizationMember.objects.filter(user=user, organization=organization).exists():
            msg = f"{email} is already a member or has a pending invite for this organization."
            log.error("existing_user", status="already_member")
            raise ValueError(msg)

        membership = OrganizationMember.objects.create(
            user=user, 
            organization=organization, 
            is_active=False # They are NOT active until they click the email link to accept invite
        )

        if role_uuid:
            role_uuid = uuid.UUID(role_uuid)
            role = Role.objects.get(uuid=role_uuid)
        else:
            role = Role.objects.get(organization=organization, is_default=True)
        
        RBACService.assign_role_to_member(caller=caller, member=membership, role=role)

        # Generate the Link Token
        # TimestampSigner is a built-in Django utility that securely signs JSON data

        signer = TimestampSigner()
        invite_payload = {
            "user_id" : str(user.uuid),
            "organization_id" : str(organization.uuid),
            "membership_id" : str(membership.uuid),
            "inviter_id" : str(caller.uuid),
            "manager_id" : str(manager_uuid)
        }
        
        accept_invite_token = signer.sign_object(invite_payload)
        log.info(
            "user_invited", 
            caller_id=caller.id, 
            invited_email=email, 
            org_id=organization.id
        )
        
        # send_invite_email.delay(email, accept_invite_token)

        return membership, accept_invite_token
