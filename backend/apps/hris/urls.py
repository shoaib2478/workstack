from django.urls import include, path
from .views import OrgCharView, EmployeeViewSet
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'employees', EmployeeViewSet, basename='employee')

urlpatterns = [
    path('org-chart/', OrgCharView.as_view(), name='org_chart'),
    path('', include(router.urls))
]