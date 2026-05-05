from django.core.cache import cache
from .models import OrganizationMember, Organization, OrganizationSetting
from django.db import transaction
from django.contrib.auth import get_user_model
from apps.rbac.models import Role, MemberRole
from apps.rbac.services import RBACService
import structlog
from rest_framework.exceptions import ValidationError
from apps.users.models import User, UserSetting
from django.utils.text import slugify
import uuid
from django.core.signing import TimestampSigner



logger = structlog.get_logger("workstack")
# User = get_user_model()


class MembershipService:
    CACHE_TTL = 60 * 15  # Cache access state for 15 minutes
    
    @staticmethod
    def _get_cache_key(user_id, organization_id):
        return f"org_access:{organization_id}:user:{user_id}"

    @classmethod
    def has_active_access(cls, user_id, organization_id):
        """
        Checks if a user has active access to an organization.
        Hits Redis first. If missing, queries Postgres and caches the result.
        """
        cache_key = cls._get_cache_key(user_id, organization_id)
        
        # 1. Try to get the boolean result directly from Redis RAM
        is_active = cache.get(cache_key)
        
        if is_active is not None:
            return is_active

        # 2. Cache Miss: Hit the Postgres Database
        try:
            membership = OrganizationMember.objects.only('is_active').get(
                user_id=user_id, 
                organization_id=organization_id
            )
            is_active = membership.is_active
        except OrganizationMember.DoesNotExist:
            is_active = False

        # 3. Store the result in Redis for the next 15 minutes
        cache.set(cache_key, is_active, timeout=cls.CACHE_TTL)
        return is_active

    @classmethod
    def invalidate_access_cache(cls, user_id, organization_id):
        """
        Call this immediately whenever an admin revokes a user's access
        so they are instantly booted out.
        """
        cache_key = cls._get_cache_key(user_id, organization_id)
        cache.delete(cache_key)


class TenantRegistrationService:
    """
    Handles the complex orchestration of provisioning a new SaaS tenant.
    All operations are atomic. If one fails, the entire transaction rolls back.
    """

    @classmethod
    @transaction.atomic
    def provision_new_tenant(cls, email: str, password: str, first_name: str, last_name: str, company_name: str) -> User:                
        if User.objects.filter(email=email).exists():
            raise ValidationError({"email": "A user with this email already exists."})
        user = User.objects.create_user(
            username=email, 
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name
         )
        user_setting = UserSetting.objects.get_or_create(user=user)


        # Use the domain prefix if available, otherwise slugify the company name
        base_slug = email.split('@')[1].split('.')[0] if '@' in email else slugify(company_name)
        # Append a random 6-character string to guarantee uniqueness
        slug = f"{base_slug}-{str(uuid.uuid4())[:6]}"
        domain = email.split('@')[1] if '@' in email else ''
        if Organization.objects.filter(domain=domain).exists():
            raise ValidationError({"company": "Your company is already registered. Please ask your administrator for an invite."})
        
        # Handle potential slug collisions in a real app (e.g., append random string if exists)
        org = Organization.objects.create(
            name=company_name,
            slug=slug,
            domain=domain
        )
                # 5. Create the Membership joining the User to the Org
        membership = OrganizationMember.objects.create(
            user=user,
            organization=org,
            is_active=True
        )
        # 3. Create Default Organization Settings
        OrganizationSetting.objects.create(organization=org)

        # 4. Create the Default "Super Admin" Role for this specific Org
        # (Later, we will map actual global permissions to this role)
        RBACService.provision_default_roles_for_org(user, org, membership)
        
        

        logger.info(
            "tenant_provisioned", 
            org_id=org.id, 
            user_id=user.id, 
            company=company_name
        )

        return user

class InviteUserService:
    """
    Handles inviting new or existing users into a specific Organization.
    """

    @classmethod
    @transaction.atomic
    def invite_user(cls, caller: User, organization: Organization, email: str, role_uuid: str = None):
        
        log = logger.bind(event_type="invite_user")
        user, user_created = User.objects.get_or_create(
            email=email,
            defaults={'username': email}
        )
        log.bind(user=user, user_created=user_created)
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
            "membership_id" : str(membership.uuid)
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

    

