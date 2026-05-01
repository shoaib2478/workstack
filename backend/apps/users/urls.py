from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .views import UserViewSet, CookieTokenObtainPairView, LogoutView, SignupView

router = DefaultRouter()

router.register(r'users', UserViewSet, basename='user')


urlpatterns = [
    # Auth Endpoints
    path('auth/login/', CookieTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('auth/signup/', SignupView.as_view(), name='signup'),
    
    # API ViewSets
    path('', include(router.urls)),
]


