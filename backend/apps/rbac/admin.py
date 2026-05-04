from django.contrib import admin
from .models import Role, MemberRole, Permission

admin.site.register(Role)
admin.site.register(MemberRole)
admin.site.register(Permission)