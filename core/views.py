from urllib.parse import urlsplit

from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.translation import get_language_from_path, gettext_lazy as _
from django.views.i18n import set_language as django_set_language

from accounts.permissions import is_employee, is_management


def set_language_fixed(request):
    """
    Wrapper around Django's set_language.

    The stock view calls translate_url(), which uses resolve() together with
    LocalePrefixPattern. That pattern consults get_language(). For POSTs to
    /i18n/setlang/ the path has no language prefix, so LocaleMiddleware forces
    the default language *before* translate_url runs — resolve('/ar/.../')
    then fails and the redirect keeps the /ar/ URL while the cookie says 'en',
    so the UI stays Arabic. Activating the language inferred from ``next``
    fixes reverse/translate for that request.
    """
    if request.method == "POST":
        next_url = request.POST.get("next") or request.GET.get("next")
        if next_url:
            path = urlsplit(next_url).path
            lang = get_language_from_path(path)
            if lang:
                translation.activate(lang)
    return django_set_language(request)


def home(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    if is_management(request.user):
        return redirect("reports:dashboard")
    if is_employee(request.user):
        return redirect("sales:employee_entry")
    return redirect("accounts:login")


def forbidden(request):
    return render(
        request,
        "core/forbidden.html",
        {"title": _("Access denied")},
        status=403,
    )
