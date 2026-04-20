from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from accounts.forms import (
    AccountPasswordForm,
    AccountSettingsForm,
    LoginForm,
    ManagementUserForm,
    UserProfileEditForm,
)
from accounts.models import UserProfile
from accounts.permissions import management_required
from accounts.query_utils import apply_user_list_ordering
from core.pagination import paginate_request

User = get_user_model()


class BilingualLoginView(LoginView):
    template_name = "accounts/login.html"
    form_class = LoginForm
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        """Drop Django's auto-populated ``site`` / ``site_name`` keys.

        ``django.contrib.auth.views.LoginView`` calls
        ``get_current_site(request)``, which falls back to the request
        host (``"127.0.0.1:8000"`` in dev) when ``django.contrib.sites``
        is not installed and stuffs that value into ``site_name``. That
        clobbers the project-wide ``site_name`` exposed by our
        ``core.context_processors.site_branding`` processor, so the
        login page ends up displaying the bind address instead of the
        branding configured by the operator. Removing the keys here
        lets the context processor's value win.
        """
        ctx = super().get_context_data(**kwargs)
        ctx.pop("site", None)
        ctx.pop("site_name", None)
        return ctx


class AppLogoutView(LogoutView):
    next_page = reverse_lazy("accounts:login")


@management_required
def user_list(request):
    qs = UserProfile.objects.select_related("user")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(user__username__icontains=q) | Q(full_name__icontains=q))
    qs = apply_user_list_ordering(request, qs)
    page_obj = paginate_request(request, qs)
    ctx = {
        "page_obj": page_obj,
        "title": _("Employees & users"),
        "sort": request.GET.get("sort") or "username",
        "order": (request.GET.get("order") or "asc").lower(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "accounts/partials/user_list_results.html", ctx)
    return render(request, "accounts/user_list.html", ctx)


@management_required
def user_create(request):
    form = ManagementUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("User created."))
        return redirect("accounts:user_list")
    return render(
        request,
        "accounts/user_form.html",
        {"form": form, "title": _("Create user")},
    )


@management_required
def user_edit(request, pk):
    profile = get_object_or_404(UserProfile.objects.select_related("user"), pk=pk)
    form = UserProfileEditForm(
        request.POST or None,
        request.FILES or None,
        instance=profile,
        user=profile.user,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("User updated."))
        return redirect("accounts:user_list")
    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
            "title": _("Edit user"),
            "editing": True,
            "target_profile": profile,
            "can_delete": _can_delete_user(request.user, profile),
        },
    )


def _can_delete_user(actor, target_profile) -> tuple[bool, str]:
    """Return (allowed, reason). Reason is a translated message when blocked."""
    target_user = target_profile.user
    if actor.pk == target_user.pk:
        return False, _("You can't delete your own account.")
    if target_user.is_superuser and not actor.is_superuser:
        return False, _("Only a superuser can delete another superuser.")
    if target_profile.role == UserProfile.Role.MANAGEMENT:
        # Don't lock everyone out: refuse to delete the last active management
        # account. Inactive ones don't count because they can't log in.
        remaining = (
            UserProfile.objects.filter(
                role=UserProfile.Role.MANAGEMENT,
                is_active_profile=True,
                user__is_active=True,
            )
            .exclude(pk=target_profile.pk)
            .count()
        )
        if remaining == 0:
            return False, _("This is the last active management account — it can't be deleted.")
    return True, ""


@management_required
def user_delete(request, pk):
    profile = get_object_or_404(UserProfile.objects.select_related("user"), pk=pk)
    allowed, reason = _can_delete_user(request.user, profile)
    if not allowed:
        messages.error(request, reason)
        return redirect("accounts:user_edit", pk=pk)
    if request.method != "POST":
        # Safety: never delete on GET. Bounce back to the edit screen so the
        # confirm action is always an explicit POST from a CSRF-protected form.
        return redirect("accounts:user_edit", pk=pk)
    deleted_username = profile.user.username
    try:
        # CASCADE on UserProfile.user removes the profile too. Sales /
        # customers / payments use PROTECT on `created_by`, so this raises
        # ProtectedError if the user is referenced anywhere we want to keep.
        profile.user.delete()
    except ProtectedError:
        messages.error(
            request,
            _(
                "Can't delete '%(name)s': they are linked to existing sales, "
                "customers or payments. Disable their login instead by "
                "unticking 'Login enabled' on the edit screen."
            )
            % {"name": deleted_username},
        )
        return redirect("accounts:user_edit", pk=pk)
    messages.success(request, _("User '%(name)s' deleted.") % {"name": deleted_username})
    return redirect("accounts:user_list")


@login_required
def account_settings(request):
    """Self-service profile + password page for the logged-in user.

    Two independent forms share the same screen. We dispatch on a hidden
    ``form`` field in the POST so each section can fail validation without
    re-rendering the other in an "errored" state, and so a successful
    password change can keep the session alive via update_session_auth_hash().
    """
    user = request.user
    profile_form = AccountSettingsForm(user=user)
    password_form = AccountPasswordForm(user=user)

    if request.method == "POST":
        which = request.POST.get("form")
        if which == "password":
            password_form = AccountPasswordForm(user=user, data=request.POST)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, password_form.user)
                messages.success(request, _("Password changed."))
                return redirect("accounts:account_settings")
        else:
            profile_form = AccountSettingsForm(
                user=user,
                data=request.POST,
                files=request.FILES,
            )
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, _("Profile updated."))
                return redirect("accounts:account_settings")

    is_management = (
        getattr(getattr(user, "profile", None), "role", "") == UserProfile.Role.MANAGEMENT
    )
    return render(
        request,
        "accounts/account_settings.html",
        {
            "title": _("Account settings"),
            "profile_form": profile_form,
            "password_form": password_form,
            "base_template": "base_management.html" if is_management else "base_employee.html",
        },
    )
