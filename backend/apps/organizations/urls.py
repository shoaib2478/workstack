# from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import InviteUserView, AcceptInviteView

urlpatterns = [
    path('invites/', InviteUserView.as_view(), name="invite_user"),
    path('invites/accept/', AcceptInviteView.as_view(), name='accept_invite_user')
]
