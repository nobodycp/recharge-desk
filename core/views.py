from urllib.parse import urlsplit

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.translation import get_language_from_path, gettext_lazy as _
from django.views.i18n import set_language as django_set_language

from accounts.permissions import is_employee, is_management, management_required
from core.forms import SiteBrandingForm
from core.models import SiteBranding


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


@management_required
def search(request):
    """One box, many models.

    The topbar search resolves to this view. We fan out across the small
    set of "things you usually look for" — sales (by reference, payer,
    or numeric id), customers (by name, phone, or id), and customer
    payments (by id or notes) — and group the results into sections.
    Each subquery is bounded to ``MAX_HITS`` rows so a stray empty
    string doesn't load the whole DB; the page also tells the user when
    a section was truncated so they can refine the query instead of
    silently missing matches.
    """
    from django.db.models import Q

    from customers.models import Customer, CustomerPayment
    from sales.models import Sale

    MAX_HITS = 25

    q = (request.GET.get("q") or "").strip()
    sales = customers = payments = []
    sale_truncated = customer_truncated = payment_truncated = False
    total = 0

    if q:
        sale_filter = Q(reference_number__icontains=q) | Q(payer_name__icontains=q)
        if q.isdigit():
            sale_filter |= Q(id=int(q))
        sale_qs = (
            Sale.objects.filter(sale_filter)
            .select_related("company", "product", "product__line", "customer", "created_by")
            .order_by("-created_at")
        )
        sales = list(sale_qs[: MAX_HITS + 1])
        sale_truncated = len(sales) > MAX_HITS
        sales = sales[:MAX_HITS]

        customer_filter = Q(name__icontains=q) | Q(phones__phone__icontains=q)
        if q.isdigit():
            customer_filter |= Q(id=int(q))
        customer_qs = (
            Customer.objects.filter(customer_filter)
            .distinct()
            .order_by("-current_balance", "name")
        )
        customers = list(customer_qs[: MAX_HITS + 1])
        customer_truncated = len(customers) > MAX_HITS
        customers = customers[:MAX_HITS]

        payment_filter = Q(notes__icontains=q) | Q(customer__name__icontains=q)
        if q.isdigit():
            payment_filter |= Q(id=int(q))
        payment_qs = (
            CustomerPayment.objects.filter(payment_filter)
            .select_related("customer", "payment_method", "created_by")
            .order_by("-created_at")
        )
        payments = list(payment_qs[: MAX_HITS + 1])
        payment_truncated = len(payments) > MAX_HITS
        payments = payments[:MAX_HITS]

        total = len(sales) + len(customers) + len(payments)

    ctx = {
        "q": q,
        "sales": sales,
        "customers": customers,
        "payments": payments,
        "sale_truncated": sale_truncated,
        "customer_truncated": customer_truncated,
        "payment_truncated": payment_truncated,
        "total": total,
        "title": _("Search"),
    }
    return render(request, "core/search.html", ctx)


@management_required
def site_branding(request):
    """Singleton editor for the company logo shown on the login page."""
    instance = SiteBranding.load()
    form = SiteBrandingForm(
        request.POST or None,
        request.FILES or None,
        instance=instance,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Branding updated."))
        return redirect("core:site_branding")
    return render(
        request,
        "core/site_branding.html",
        {"form": form, "branding": instance, "title": _("Site branding")},
    )
