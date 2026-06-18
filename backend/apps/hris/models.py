"""
Materialized Path
Instead of just pointing to a manager, every employee stores their exact "path" from the CEO.

CEO: 0001

VP of Eng: 00010002

Engineering Manager: 000100020005

Junior Dev: 0001000200050010

Why this is a massive interview flex: If the VP of Eng wants to query all 5,000 engineers under them,
the SQL is simply: SELECT * FROM employees WHERE path LIKE '00010002%'. 
It uses a B-Tree database index and returns in milliseconds without any recursion.
"""
from django.db import models
from treebeard.mp_tree import MP_Node
from apps.organizations.models import Organization
from django.conf import settings

class Department(models.Model):
    """Simple lookup table for departments (e.g., Engineering, Sales)"""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name

class Employee(MP_Node):
    """
    [SYSTEM DESIGN]: Materialized Path Tree
    Inheriting from MP_Node automatically adds 3 columns to our DB:
    1. `path` (VARCHAR): e.g., '000100020005'
    2. `depth` (INT): e.g., 3 (Level 3 in the org chart)
    3. `numchild` (INT): How many direct reports this person has.
    
    [SOLID]: Single Responsibility Principle (SRP)
    This class has ONE reason to change: Database Schema updates. 
    It does NOT contain business logic for moving employees or calculating 
    payroll. It simply defines the shape of the data.
    """

    # 1. Tenant Isolation (Multi-tenant SaaS Architecture)
    organization = models.ForeignKey(
        Organization, 
        on_delete=models.CASCADE, 
        related_name='employees'
    )

    # 2. Identity (Who is this?)
    # We link to the User model. If a user is deactivated, the employee profile remains 
    # but is marked inactive, or handled via SET_NULL depending on data retention laws.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='employee_profile'
    )
    
    # 3. Business Context (What do they do?)
    job_title = models.CharField(max_length=255)
    department = models.ForeignKey(
        Department, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='employees'
    )

    is_active = models.BooleanField(default=True)

    # [TREEBEARD CONFIGURATION]
    # Defines how siblings (people with the same manager) are ordered in the DB.
    node_order_by = ['job_title']


    class Meta:
        # [SYSTEM DESIGN]: Database Indexing Strategy
        # Since this is a multi-tenant app, EVERY query will filter by `organization`.
        # By creating a composite index on (organization, path), PostgreSQL can 
        # instantly find a subtree for a specific company using a B-Tree index scan.
        indexes = [
            models.Index(fields=['organization', 'path']),
            models.Index(fields=['organization', 'depth']),
        ]
        # Ensure a user is only one employee per organization
        constraints = [
            models.UniqueConstraint(fields=['organization', 'user'], name='unique_org_employee')
        ]
    @property
    def manager(self):
        return self.get_parent()
        
    def __str__(self):
        return f"{self.user.get_full_name()} ({self.job_title})"
