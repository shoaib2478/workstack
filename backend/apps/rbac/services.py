from django.core.cache import cache
from apps.rbac.models import MemberRole, Role
from apps.organizations.models import OrganizationMember
from apps.rbac.constants import DEFAULT_ROLES
import structlog
logger = structlog.get_logger("workstack")
# logger.info("employee_created", user_id=123, org_id=45)

class RBACService:
    """
    Service Layer handling all Role-Based Access Control logic.
    Utilizes the [Cache-Aside Pattern] for maximum read performance.
    """
    CACHE_TTL = 60 * 60  # Cache permissions for 1 hour
    
    @staticmethod
    def _get_cache_key(user_id: int, organization_id: int) -> str:
        """[DRY] Centralized cache key generation."""
        return f"org:{organization_id}:user:{user_id}:permissions"

    @classmethod
    def get_user_permissions(cls, user_id: int, organization_id: int) -> set:
        """
        Retrieves all permission codes for a user within a specific org.
        Hits Redis RAM first (~1ms). If miss, hits Postgres (~10ms).
        """
        cache_key = cls._get_cache_key(user_id, organization_id)        
        
        cached_perms = cache.get(cache_key)
        if cached_perms is not None:
            return cached_perms

        try:
            membership = OrganizationMember.objects.get(
                user_id=user_id, 
                organization_id=organization_id,
                is_active=True # Security: Ensure they aren't deactivated
            )
        except OrganizationMember.DoesNotExist:
            return set()

        # [Scaling] Use values_list to only pull the specific 'code' string from the DB, 
        # avoiding the overhead of instantiating full Django Model objects.
        permissions_query = MemberRole.objects.filter(
            member=membership
        ).values_list('role__permissions__code', flat=True)

        # Convert to a set for O(1) lookup time later, and remove any Nones
        permissions_set = {code for code in permissions_query if code}
        
        cache.set(cache_key, permissions_set, timeout=cls.CACHE_TTL)
        
        return permissions_set

    @classmethod
    def has_permission(cls, user_id: int, organization_id: int, permission_code: str) -> bool:
        """
        The primary gateway method used by our API views/permissions classes.
        """
        user_perms = cls.get_user_permissions(user_id, organization_id)
        return permission_code in user_perms

    @classmethod
    def invalidate_cache(cls, user_id: int, organization_id: int) -> None:
        """
        [SRP] Call this method anytime an Admin changes a user's role,
        ensuring the cache is instantly purged and security is maintained.
        """
        cache.delete(cls._get_cache_key(user_id, organization_id))

    @classmethod

    @classmethod
    def provision_default_roles_for_org(cls, organization_id, membership):
        """[SRP] Handles seeding default roles and permissions for a new tenant."""
        for role_name, role_description in DEFAULT_ROLES.items():
            role = Role.objects.create(
                organization_id=organization_id,
                name=role_name,
                description=role_description
            )
            MemberRole.objects.create(member=membership, role=role)



        # 3. TODO: Handle Permissions. 
        # TODO: In a real app, you might query all existing Permissions and attach them:
        # TODO: all_perms = Permission.objects.all()
        # TODO: admin_role.permissions.set(all_perms)