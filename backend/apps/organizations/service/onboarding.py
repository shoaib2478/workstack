from django.core.cache import cache
from apps.organizations.models import OrganizationMember, Organization, OrganizationSetting
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