from rest_framework.decorators import action
from rest_framework.views import  APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.hris.models import Employee
from .serializers import EmployeeSerializer
from apps.organizations.mixin import OrganizationMixin
from rest_framework import viewsets
from django.shortcuts import get_object_or_404
import structlog


logger = structlog.get_logger('workstack')

class EmployeeViewSet(OrganizationMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EmployeeSerializer

    lookup_field = 'user__uuid'
    lookup_url_kwarg = 'uuid'

    def get_queryset(self):
        return Employee.objects.select_related('user', 'department').filter(organization=self.organization).order_by('path')

    # not needed now moved to EmployeeSerializer
    # def _format_employee(self, emp):        
    #     return {
    #         "uuid": str(emp.user.uuid),
    #         "name": emp.user.get_full_name(),
    #         "job_title": emp.job_title,
    #         "department_name": emp.department.name if emp.department else None,
    #         "is_active": emp.is_active,
    #         "has_children": emp.numchild > 0, # For FE if it should show the "+" expand button!
    #     }

    @action(detail=True, methods=['get'], url_path='direct-reports')
    def direct_reports(self, request, uuid=None):
        """
        GET /api/v1/hris/employees/{uuid}/direct-reports/
        Solves the 20k user problem. Fetches only the immediate children.
        """
        log = logger.bind(event_type='get_direct_reports')
        
        manager: Employee = self.get_object()
        log = log.bind(manager_id=manager.id)
        children = manager.get_children().select_related('user', 'department')
        serializer = self.get_serializer(children, many=True)
        return Response(serializer.data)
        # return Response([self._format_employee(child) for child in children])

    
    @action(detail=True, methods=['get'], url_path='ancestors')
    def reporting_chain(self, request, uuid=None):
        """
        GET /api/v1/hris/employees/{uuid}/ancestors/
        Returns the path from the CEO down to this specific employee.
        """
        employee = self.get_object()
        
        # Treebeard method: get_ancestors()
        ancestors = employee.get_ancestors().select_related('user', 'department')
        serializer = self.get_serializer(ancestors, many=True)
        return Response(serializer.data)
        # return Response([self._format_employee(anc) for anc in ancestors])

    @action(detail=False, methods=['get'], url_path='lcm')
    def least_common_manager(self, request):
        """
        GET /api/v1/hris/employees/lcm/?emp1={uuid}&emp2={uuid}
        The classic LeetCode problem, solved instantly using Materialized Paths.
        """
        uuid1 = request.query_params.get('emp1')
        uuid2 = request.query_params.get('emp2')

        if not uuid1 or not uuid2:
            return Response({"error": "Please provide emp1 and emp2 UUIDs."}, status=400)

        emp1 = get_object_or_404(self.get_queryset(), user__uuid=uuid1)
        emp2 = get_object_or_404(self.get_queryset(), user__uuid=uuid2)

        # The System Design Flex: String Prefix Matching
        # e.g., emp1='000100020005', emp2='000100020008' -> Common string is '00010002'
        path1 = emp1.path
        path2 = emp2.path
        steplen = Employee.steplen # Usually 4
        
        common_path = ""
        # Compare chunks of 'steplen' characters
        for i in range(0, min(len(path1), len(path2)), steplen):
            chunk1 = path1[i:i+steplen]
            chunk2 = path2[i:i+steplen]
            if chunk1 == chunk2:
                common_path += chunk1
            else:
                break

        if not common_path:
            return Response({"error": "No common manager found (broken tree)."}, status=404)

        # Fetch the manager using the exact calculated path. O(1) B-Tree lookup!
        lcm_node = get_object_or_404(self.get_queryset(), path=common_path)
        serializer = self.get_serializer(lcm_node)
        return Response(serializer.data)
        
        # return Response(self._format_employee(lcm_node))



class OrgCharView(OrganizationMixin, APIView):
    """
    Fetches the entire employee hierarchy for the current organization
    and serializes it into a nested JSON tree for the frontend.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        DON't use this as it required n + 1 query to get user and org uuid to send back to frontend
        Use Treebeard's highly optimized dump_bulk to get the nested tree        # 
        This executes in a SINGLE database query!
        tree_data = Employee.dump_bulk(parent=root_node, keep_ids=True)
        formatted_tree = self._format_node(tree_data[0])
        """
        root_node = Employee.get_root_nodes().filter(organization = self.organization).first()        

        if not root_node:
            return Response({"detail" : "Organization employee chart has not setup yet."})

        
        # Get employees in O(1) Query
        # We fetch all employees for this org, and JOIN the User and Department tables instantly.
        # We order by 'path' so that parents ALWAYS appear before their children.

        employees = Employee.objects.select_related('user', 'department').filter(
            organization=self.organization
        ).order_by('path')
        if not employees.exists():
                return Response({"detail": "Org chart is empty."}, status=404)

            # 2. The In-Memory Tree Builder
        tree = []
        node_map = {}
        steplen = Employee.steplen  # Treebeard's step length (usually 4)

        for emp in employees:
            # Build the secure React-friendly dictionary
            node = {
                "uuid": str(emp.user.uuid), 
                "email": emp.user.email,
                "name": emp.user.get_full_name(), 
                "job_title": emp.job_title,
                "department_uuid": str(emp.department.uuid) if emp.department else None,
                "department_name": emp.department.name if emp.department else None,
                "is_active": emp.is_active,
                "children": []
            }
            
            # Save a reference to this node in our hash map using its unique Treebeard path
            node_map[emp.path] = node

            if emp.depth == 1:
                # If depth is 1, they are a Root Node (CEO). Add them to the top of the tree.
                tree.append(node)
            else:
                # If they are not a root, we find their manager's path.
                # Treebeard paths work like this: if child is '00010001', parent is '0001'
                parent_path = emp.path[:-steplen]
                
                # Append this employee to their manager's 'children' array
                if parent_path in node_map:
                    node_map[parent_path]['children'].append(node)
        
        return Response(tree)
        

    # def _format_node(self, node):
    #     """
    #     Recursively formats the Treebeard dump_bulk dictionary into a clean React payload.
    #     """
    #     data = node.get('data', {})
        
    #     # Build the current employee object
    #     formatted = {
    #         "id": node.get('id'), # Avpid sending primary key to prevent IDOR (Insecure Direct Object Reference) attacks
    #         "job_title": data.get('job_title'),
    #         "department_id": data.get('department_id'),
    #         "is_active": data.get('is_active'),
    #         # You would normally inject the User's name/email here using a Serializer,
    #         # but for performance on massive trees, it's best to join them at the DB level
    #         # or fetch user details in a separate dictionary.
    #         "children": []
    #     }

    #     # Recursively process children
    #     if 'children' in node:
    #         for child in node['children']:
    #             formatted['children'].append(self._format_node(child))

    #     return formatted