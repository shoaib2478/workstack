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






    

