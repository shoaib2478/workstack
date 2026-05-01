from django.core.cache import cache
from .models import OrganizationMember, Organization, OrganizationSetting
from django.db import transaction
from django.contrib.auth import get_user_model
from apps.rbac.models import Role, MemberRole
import structlog

logger = structlog.get_logger("workstack")
User = get_user_model()


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
        user = User.objects.create_user(
            username=email, 
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name
         )
    
        # 2. Create the Organization (Generate a simple slug from the name)
        slug = company_name.lower().replace(" ", "-").replace(".", "")
        # Handle potential slug collisions in a real app (e.g., append random string if exists)
        org = Organization.objects.create(
            name=company_name,
            slug=slug,
            domain=email.split('@')[1] if '@' in email else ''
        )

        # 3. Create Default Organization Settings
        OrganizationSetting.objects.create(organization=org)

        # 4. Create the Default "Super Admin" Role for this specific Org
        # (Later, we will map actual global permissions to this role)
        admin_role = Role.objects.create(
            organization=org,
            name="Super Admin",
            description="Full unrestricted access to all platform features."
        )
        
        # We also seed a default "Employee" role for them to use later
        Role.objects.create(
            organization=org,
            name="Standard Employee",
            description="Basic access to personal profile and company directory."
        )

        # 5. Create the Membership joining the User to the Org
        membership = OrganizationMember.objects.create(
            user=user,
            organization=org,
            is_active=True
        )

        # 6. Assign the Super Admin Role to this Membership
        MemberRole.objects.create(
            member=membership,
            role=admin_role
        )

        logger.info(
            "tenant_provisioned", 
            org_id=org.id, 
            user_id=user.id, 
            company=company_name
        )

        return user