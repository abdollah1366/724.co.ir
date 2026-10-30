from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import LoginForm

app_name = "accounts"

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="accounts/login.html", authentication_form=LoginForm, redirect_authenticated_user=True),
        name="login",
    ),
    path("logout/", views.StaffLogoutView.as_view(), name="logout"),
    path("password/", views.PasswordChangeView.as_view(), name="password_change"),
    path("rewards/<int:pk>/redeem/", views.redeem, name="redeem"),
    path("", views.dashboard, name="dashboard"),
]
