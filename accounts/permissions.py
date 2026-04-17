from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from accounts.models import UserProfile


def get_profile(user):
    if not user.is_authenticated:
        return None
    return getattr(user, "profile", None)


def is_management(user) -> bool:
    if user.is_superuser:
        return True
    profile = get_profile(user)
    return bool(profile and profile.role == UserProfile.Role.MANAGEMENT and profile.is_active_profile)


def is_employee(user) -> bool:
    profile = get_profile(user)
    return bool(profile and profile.role == UserProfile.Role.EMPLOYEE and profile.is_active_profile)


def management_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not is_management(request.user):
            return redirect("core:forbidden")
        return view_func(request, *args, **kwargs)

    return _wrapped


def employee_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not (is_employee(request.user) or is_management(request.user)):
            return redirect("core:forbidden")
        return view_func(request, *args, **kwargs)

    return _wrapped
