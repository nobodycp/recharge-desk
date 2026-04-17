from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.BilingualLoginView.as_view(), name="login"),
    path("logout/", views.AppLogoutView.as_view(), name="logout"),
    path("management/users/", views.user_list, name="user_list"),
    path("management/users/new/", views.user_create, name="user_create"),
    path("management/users/<int:pk>/edit/", views.user_edit, name="user_edit"),
]
