from django.urls import path

from . import views

urlpatterns = [
    path("auth/login/", views.login_view, name="auth-login"),
    path("auth/logout/", views.logout_view, name="auth-logout"),
    path("auth/me/", views.me_view, name="auth-me"),
    path("approvals/request/", views.request_approval_view, name="approval-request"),
]
