from django.contrib import admin
from .models import Permission, Role, RolePermission, MemberRole

@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ('code', 'description')
    search_fields = ('code', 'description')
    ordering = ('code',)

# Inline for RolePermission (Shows permissions inside the Role page)
class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 1
    # Make audit fields read-only so support staff can't fake the history
    readonly_fields = ('granted_by', 'last_modified_by', 'created_at')
    autocomplete_fields = ['permission']

# Inline for MemberRole (Shows who has the role inside the Role page)
class MemberRoleInline(admin.TabularInline):
    model = MemberRole
    extra = 0
    readonly_fields = ('granted_by', 'created_at')

@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization', 'created_at')
    list_filter = ('organization',)
    search_fields = ('name', 'organization__name')
    inlines = [RolePermissionInline, MemberRoleInline]    
    
    raw_id_fields = ('organization',)

@admin.register(MemberRole)
class MemberRoleAdmin(admin.ModelAdmin):
    """Also register it standalone in case you need to query directly."""
    list_display = ('member', 'role', 'granted_by', 'created_at')
    list_filter = ('role__organization', 'role')
    search_fields = ('member__user__email', 'role__name')
    raw_id_fields = ('member', 'role', 'granted_by')