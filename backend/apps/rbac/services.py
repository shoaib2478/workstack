from django.core.cache import cache
from apps.rbac.models import MemberRole, Role, RolePermission, Permission
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

        # User Role Permissions
        # member_roles = MemberRole.objects.filter(member=membership)
        # role_permissions = RolePermission.objects.filter(role__in=member_roles.values_list('role', flat=True))
        # permissions_set = {permission.permission.code for permission in role_permissions}
        permissions_query = RolePermission.objects.filter(
            role__member_assignments__member=membership
        ).values_list('permission__code', flat=True)
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
    def add_permission(cls, code: str,  description: str = '') -> None:
        """Global permissions are system-wide, so no caller/org is needed."""
        permission, created = Permission.objects.get_or_create(
            code=code,
            defaults={'description': description}
        )
        logger.info("Permission added", permission=permission, created=created)
        return permission
    
    @classmethod
    def add_role(cls, organization, name: str, description: str = None, is_system: bool = False, is_default: bool = False) -> Role:
        """Creates a Role specific to an Organization."""
        role, created = Role.objects.get_or_create(
            organization=organization,
            name=name,
            defaults={
                'description': description,
                'is_system': is_system,
                'is_default': is_default
                }
        )
        logger.info("Role added", role=role, created=created)
        return role

    @classmethod
    def add_rolepermission(cls, caller, role: Role, permission: Permission) -> RolePermission:
        """
        [Audit Trail]: Explicitly maps a permission to a role, tracking WHO did it.
        """
        role_perm, created = RolePermission.objects.get_or_create(
            role=role,
            permission=permission,
            defaults={'granted_by': caller, 'last_modified_by': caller}
        )
        
        if not created:
            # If it already existed, just update the modifier
            role_perm.last_modified_by = caller
            role_perm.save(update_fields=['last_modified_by', 'updated_at'])
            
        return role_perm

    @classmethod
    def assign_role_to_member(cls, caller, member: OrganizationMember, role: Role) -> MemberRole:
        """
        [Audit Trail]: Assigns a role to an employee, tracking WHO assigned it.
        Invalidates their cache instantly.
        """
        member_role, created = MemberRole.objects.get_or_create(
            member=member,
            role=role,
            defaults={'granted_by': caller}
        )
        logger.info("Member role assigned", member_role=member_role, created=created)
        # Security: Immediately bust the cache so they get the new permissions
        cls.invalidate_cache(member.user_id, member.organization_id)
        return member_role

  
    @classmethod
    def provision_default_roles_for_org(cls, caller, organization, super_admin_member):
        """[SRP] Handles seeding default roles and permissions for a new tenant."""
        
        # 1. Define the Global Taxonomy (Usually done in a setup script, 
        # but safe to run here using get_or_create)
        system_permissions = {
            'users:read': cls.add_permission('users:read', 'View Users'),
            'users:write': cls.add_permission('users:write', 'Manage Users'),
            'roles:read': cls.add_permission('roles:read', 'View Roles'),
            'roles:write': cls.add_permission('roles:write', 'Manage Roles'),
            'org:read': cls.add_permission('org:read', 'View Organization'),
            'org:write': cls.add_permission('org:write', 'Manage Organization'),
        }

        # 2. Create the Roles
        admin_role_name  = "Super Admin"
        admin_role_desc = DEFAULT_ROLES.get(admin_role_name)
        std_role_name = "Standard Employee"
        std_role_desc = DEFAULT_ROLES.get(std_role_name)
        admin_role = cls.add_role(organization, admin_role_name, admin_role_desc, is_system=True, is_default=False)
        employee_role = cls.add_role(organization, std_role_name, std_role_desc, is_system=True, is_default=True) # <-- This makes it the fallback role!

        
        for perm_code, perm_obj in system_permissions.items():
            cls.add_rolepermission(caller=caller, role=admin_role, permission=perm_obj)

        # 4. Assign Permissions to Standard Employee (View only)
        employee_perms = ['users:read', 'org:read']
        for perm_code in employee_perms:
            cls.add_rolepermission(caller=caller, role=employee_role, permission=system_permissions[perm_code])

        # 5. Finally, attach the Super Admin role to the founding member
        cls.assign_role_to_member(caller=caller, member=super_admin_member, role=admin_role)