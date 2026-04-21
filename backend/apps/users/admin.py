from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import UserSetting, User

User = get_user_model()


class UserAdmin(BaseUserAdmin):
    

    list_display = ("username", "email", "is_superuser",)
    list_filter = ("is_active", )

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (
            "Persenol Info",
            {
                "fields": (
                    "email",
                    "full_name",
                    "phone_number_primary",
                    "phone_number_secondary",
                )
            },
        ),
        ("Permissions", {"fields": ("staff", "is_superuser")}),
        ("Status", {"fields": ("active",)}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "email", "password1", "password2"),
            },
        ),
    )

    search_fields = ("username",)
    ordering = ("username",)
    filter_horizontal = ()


admin.site.register(User)
admin.site.register(UserSetting)
