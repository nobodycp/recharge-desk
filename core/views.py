from urllib.parse import urlsplit

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.translation import get_language_from_path, gettext_lazy as _
from django.views.i18n import set_language as django_set_language

from accounts.permissions import is_employee, is_management, management_required
from core.forms import AppSettingsForm, SiteBrandingForm
from core.models import AppSettings, SiteBranding


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
def nav_notifications_poll(request):
    """Lightweight JSON heartbeat for the topbar notification badge.

    Returns the same {awaiting, pending, submissions, total} payload the context
    processor renders into the page on initial load, so the JS poller
    can reuse the existing markup without an extra translation step.
    Two indexed ``COUNT(*)`` queries — cheap enough to poll every
    15 s without measurable impact even with several active users.
    """
    from core.context_processors import compute_nav_notifications

    counts = compute_nav_notifications(request.user) or {
        "awaiting": 0,
        "pending": 0,
        "submissions": 0,
        "total": 0,
    }
    return JsonResponse(
        {
            **counts,
            "labels": {
                "needs_attention": str(_("Needs attention")),
                "all_caught_up": str(_("All caught up.")),
            },
        }
    )


@management_required
def search_suggest(request):
    """Tiny JSON endpoint feeding the live-suggestion dropdown in the topbar.

    Returns at most ``PER_GROUP`` rows from each of the three sections so
    the dropdown stays scannable; the payload is intentionally
    URL-and-label only (no full row markup) so the wire format is
    cacheable and the rendering work stays in the browser.
    """
    from django.db.models import Q

    from customers.models import Customer, CustomerPayment
    from sales.models import Sale

    PER_GROUP = 6
    q = (request.GET.get("q") or "").strip()
    groups = []

    if len(q) >= 2:
        sale_filter = Q(reference_number__icontains=q) | Q(payer_name__icontains=q)
        if q.isdigit():
            sale_filter |= Q(id=int(q))
        sale_rows = (
            Sale.objects.filter(sale_filter)
            .select_related("company", "product__line", "customer")
            .order_by("-created_at")[:PER_GROUP]
        )
        sales = [
            {
                "label": s.reference_number or f"#{s.pk}",
                "sublabel": " · ".join(
                    bit
                    for bit in [
                        s.payer_name,
                        s.company.name if s.company_id else "",
                        s.product.display_name if s.product_id else "",
                    ]
                    if bit
                ),
                "url": reverse("sales:management_sale_list") + f"?q={s.reference_number or s.pk}",
            }
            for s in sale_rows
        ]
        if sales:
            groups.append({"key": "sales", "label": str(_("Sales")), "items": sales})

        customer_filter = Q(name__icontains=q) | Q(phones__phone__icontains=q)
        if q.isdigit():
            customer_filter |= Q(id=int(q))
        customer_rows = (
            Customer.objects.filter(customer_filter)
            .distinct()
            .order_by("-current_balance", "name")[:PER_GROUP]
        )
        customers = [
            {
                "label": c.name,
                "sublabel": (
                    str(_("Owes")) + f" {c.current_balance}"
                    if c.current_balance > 0
                    else (
                        str(_("Credit")) + f" {abs(c.current_balance)}"
                        if c.current_balance < 0
                        else str(_("Settled"))
                    )
                ),
                "url": reverse("customers:customer_detail", args=[c.pk]),
            }
            for c in customer_rows
        ]
        if customers:
            groups.append({"key": "customers", "label": str(_("Customers")), "items": customers})

        payment_filter = Q(notes__icontains=q) | Q(customer__name__icontains=q)
        if q.isdigit():
            payment_filter |= Q(id=int(q))
        payment_rows = (
            CustomerPayment.objects.filter(payment_filter)
            .select_related("customer", "payment_method")
            .order_by("-created_at")[:PER_GROUP]
        )
        payments = [
            {
                "label": f"#{p.pk} · {p.customer.name}",
                "sublabel": " · ".join(
                    bit
                    for bit in [
                        f"{p.amount}",
                        p.payment_method.name if p.payment_method_id else "",
                        p.created_at.strftime("%Y-%m-%d"),
                    ]
                    if bit
                ),
                "url": reverse("customers:customer_detail", args=[p.customer_id]) + f"#payment-{p.pk}",
            }
            for p in payment_rows
        ]
        if payments:
            groups.append({"key": "payments", "label": str(_("Payments")), "items": payments})

    return JsonResponse(
        {
            "q": q,
            "groups": groups,
            "more_url": reverse("core:search") + (f"?q={q}" if q else ""),
            "more_label": str(_("See all results")),
            "empty_label": str(_("No results")),
            "hint_label": str(_("Type at least 2 characters…")),
        }
    )


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


@management_required
def system_settings(request):
    """Singleton editor for recharge-desk behaviour and UI defaults."""
    instance = AppSettings.load()
    form = AppSettingsForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("System settings updated."))
        return redirect("core:system_settings")
    return render(
        request,
        "core/system_settings.html",
        {"form": form, "settings": instance, "title": _("System settings")},
    )
