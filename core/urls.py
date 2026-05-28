from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("forbidden/", views.forbidden, name="forbidden"),
    path("search/", views.search, name="search"),
    path("search/suggest/", views.search_suggest, name="search_suggest"),
    path("notifications/poll/", views.nav_notifications_poll, name="nav_notifications_poll"),
    path("management/branding/", views.site_branding, name="site_branding"),
    path("management/system-settings/", views.system_settings, name="system_settings"),
]
