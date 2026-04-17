from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _

from accounts.forms import LoginForm, ManagementUserForm, UserProfileEditForm
from accounts.models import UserProfile
from accounts.permissions import management_required
from accounts.query_utils import apply_user_list_ordering
from core.pagination import paginate_request

User = get_user_model()


class BilingualLoginView(LoginView):
    template_name = "accounts/login.html"
    form_class = LoginForm
    redirect_authenticated_user = True


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
        {"form": form, "title": _("Edit user"), "editing": True},
    )
