from django.db import transaction
from apps.organizations.models import Organization
from django.contrib.auth import get_user_model
from apps.hris.models import Employee

User = get_user_model()

class OrgChartService:
    """
    [DESIGN PATTERN]: Service Layer / Facade
    This class provides a simple API for complex Treebeard operations.
    The rest of the application doesn't need to know how Materialized Paths work.
    """

    @classmethod
    @transaction.atomic
    def add_employee(cls, organization: Organization, user: User, job_title: str, manager_node: Employee = None) -> Employee:
        """
        Adds a new employee to the tree. 
        If manager_node is None, they become a Root Node (e.g., CEO).
        """
        # 1. Prepare the business data payload
        employee_data = {
            'organization': organization,
            'user': user,
            'job_title': job_title,
            'is_active': True
        }

        # 2. Treebeard Mathematics
        if manager_node is None:
            # No manager? Create a new Root Node (Depth = 1)
            # Treebeard calculates the new path automatically (e.g., '0002')
            employee = Employee.add_root(**employee_data)
        else:
            # Has a manager? Append as a child.
            # Treebeard reads the manager's path ('0001') and generates the child path ('00010001')
            employee = manager_node.add_child(**employee_data)
            print("New employee added -------------------- " , employee)
        return employee

    @classmethod
    @transaction.atomic
    def move_employee(cls, employee: Employee, new_manager: Employee) -> Employee:
        """
        Moves an employee (and their entire subtree of reports) to a new manager.
        """
        # [SYSTEM DESIGN]: Concurrency Control
        # Re-orgs are dangerous. If two admins move employees at the exact same millisecond,
        # the tree paths can corrupt. @transaction.atomic ensures this is thread-safe.
        
        # Treebeard handles updating the `path` string for this employee AND 
        # cascades the path update to all descendants automatically!
        employee.move(new_manager, pos='sorted-child')
        
        # Reload from DB to get the newly calculated path
        employee.refresh_from_db() 
        return employee

    @classmethod
    def get_reporting_chain(cls, employee: Employee):
        """
        [DFS Equivalent]: Returns the path from the CEO down to this employee.
        Because of Materialized Paths, this does NOT require recursion.
        Treebeard just chops the path string into chunks and does a single SQL `IN` query.
        """
        return employee.get_ancestors()

    @classmethod
    def get_all_descendants(cls, manager: Employee):
        """
        Returns every single person under this manager, all the way to the bottom.
        SQL Equivalent: SELECT * FROM employee WHERE path LIKE '00010002%'
        Time Complexity: O(1) index lookup.
        """
        return manager.get_descendants()