from rest_framework.pagination import CursorPagination
class EmployeeCursorPagination(CursorPagination):
    page_size = 100
    # ordering = '-created_at'
    ordering = 'path' # The B-Tree indexed column!
    cursor_query_param = 'cursor'
    ordering_field = 'path'
    ordering_direction = 'asc'
    ordering_field_map = {
        'path': 'path',
    }
    ordering_direction_map = {
        'asc': '',
    }