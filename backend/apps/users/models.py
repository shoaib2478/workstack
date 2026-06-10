import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from core.models import BaseTimeStampedModel
from django.conf import settings

class User(AbstractUser):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # --- FIX: Override fields to prevent reverse accessor clashes ---
    groups = models.ManyToManyField(
        'auth.Group',
        verbose_name='groups',
        blank=True,
        help_text='The groups this user belongs to.',
        related_name="custom_user_groups",  # Unique related name
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        verbose_name='user permissions',
        blank=True,
        help_text='Specific permissions for this user.',
        related_name="custom_user_permissions", # Unique related name
    )

    def __str__(self):
        return self.email or self.username

class UserSetting(BaseTimeStampedModel):    
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='settings'
    )
    theme = models.CharField(max_length=20, default='system', help_text="'light', 'dark', or 'system'")
    language = models.CharField(max_length=10, default='en')
    timezone = models.CharField(max_length=50, default='UTC')
    receive_email_notifications = models.BooleanField(default=True)

    def __str__(self):
        return f"Settings for {self.user}"

