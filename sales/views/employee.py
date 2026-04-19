"""Employee-facing sales views: entry form, JSON helpers, product fragment."""

from django.contrib import messages
from django.db import DatabaseError, IntegrityError
from django.db.models import Prefetch
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import employee_required, is_employee
from companies.models import Company, Product, ProductLine
from customers.services import resolve_or_create_customer_for_sale
from sales.forms import EmployeeRecentFilterForm, EmployeeSaleForm, ManagementSaleEditForm
from sales.models import PaymentMethod, Sale
from sales.payer_lookup import latest_sale_for_reference, payer_name_suggestions
from sales.pricing import ESIM_EXTRA_COST
from sales.services import create_sale, delete_sale_permanently, update_sale_fields
from sales.views._shared import htmx_action_error, htmx_remove_target, is_htmx


def _build_product_groups(company_id):
    lines_qs = (
        ProductLine.objects.filter(company_id=company_id, is_active=True)
        .select_related("default_package")
        .prefetch_related(
            Prefetch(
                "variants",
                queryset=Product.objects.filter(is_active=True).select_related("line"),
            )
        )
        .order_by("sort_order", "name")
    )
    groups = []
    for line in lines_qs:
        variants = list(line.variants.all())
        if variants:
            variant_ids = {p.pk for p in variants}
            default_product_id = ""
            if line.default_package_id and line.default_package_id in variant_ids:
                default_product_id = str(line.default_package_id)
            groups.append(
                {"line": line, "variants": variants, "default_product_id": default_product_id}
            )
    return groups


@employee_required
def employee_entry(request):
    company_id = request.POST.get("company") or request.GET.get("company")
    initial = {}
    if request.method == "GET" and company_id:
        initial["company"] = company_id
    form = EmployeeSaleForm(request.POST or None, company_id=company_id, initial=initial)
    companies = list(Company.objects.filter(is_active=True).order_by("name"))
    payment_methods = list(PaymentMethod.objects.filter(is_active=True).order_by("name"))
    product_groups = _build_product_groups(company_id) if company_id else []

    recent_qs = Sale.objects.select_related("company", "product", "product__line", "payment_method")
    if is_employee(request.user):
        recent_qs = recent_qs.filter(created_by=request.user)
    recent = recent_qs.order_by("-created_at")[:8]

    if request.method == "POST" and form.is_valid():
        try:
            on_account = bool(form.cleaned_data.get("on_account"))
            customer = None
            if on_account:
                customer = resolve_or_create_customer_for_sale(
                    name=form.cleaned_data["payer_name"],
                    phone=form.cleaned_data["reference_number"],
                    user=request.user,
                )
            create_sale(
                company=form.cleaned_data["company"],
                product=form.cleaned_data["product"],
                reference_number=form.cleaned_data["reference_number"],
                payer_name=form.cleaned_data["payer_name"],
                payment_method=form.cleaned_data["payment_method"] if not on_account else None,
                sell_price_actual=form.cleaned_data["sell_price_actual"],
                notes=form.cleaned_data.get("notes") or "",
                user=request.user,
                is_esim=bool(form.cleaned_data.get("is_esim")),
                on_account=on_account,
                customer=customer,
            )
            if on_account:
                messages.success(request, _("Recorded as on-account; awaiting management approval."))
            else:
                messages.success(request, _("Sale recorded successfully."))
            return redirect("sales:employee_entry")
        except ValueError as exc:
            messages.error(request, str(exc))

    selected_line_id = ""
    selected_product_id = ""
    raw_pid = (request.POST.get("product") or "").strip()
    if not raw_pid and getattr(form, "data", None):
        raw_pid = (form.data.get("product") or "").strip()
    if raw_pid:
        selected_product_id = raw_pid
        try:
            selected_line_id = str(Product.objects.only("line_id").get(pk=int(raw_pid)).line_id)
        except (ValueError, Product.DoesNotExist):
            pass

    return render(
        request,
        "sales/employee_entry.html",
        {
            "form": form,
            "title": _("New sale"),
            "recent_sales": recent,
            "product_groups": product_groups,
            "companies": companies,
            "payment_methods": payment_methods,
            "selected_company_id": str(company_id or ""),
            "selected_line_id": selected_line_id,
            "selected_product_id": selected_product_id,
            "selected_payment_id": str(request.POST.get("payment_method", "") or ""),
            "esim_extra": ESIM_EXTRA_COST,
        },
    )


@employee_required
@require_GET
def api_payer_by_number(request):
    """JSON: latest sale snapshot for an exact phone/shipment match."""
    empty = {"payer_name": None, "company_id": None, "product_id": None}
    number = (request.GET.get("number") or "").strip()
    if len(number) < 3:
        return JsonResponse(empty)
    snap = latest_sale_for_reference(number)
    if not snap:
        return JsonResponse(empty)
    return JsonResponse(
        {
            "payer_name": snap.get("payer_name"),
            "company_id": snap.get("company_id"),
            "product_id": snap.get("product_id"),
        }
    )


@employee_required
@require_GET
def api_payer_name_suggestions(request):
    """JSON: distinct historical payer names matching q (for autocomplete)."""
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"suggestions": []})
    items = payer_name_suggestions(q, limit=10)
    return JsonResponse({"suggestions": [{"name": x["name"], "count": x["count"]} for x in items]})


def _own_sales_qs(user, *, date_from=None, date_to=None):
    """All sales the given user created within an optional date range,
    freshest first. ``date_from`` / ``date_to`` are inclusive ``date``
    objects in the local timezone."""
    qs = Sale.objects.select_related(
        "company", "product", "product__line", "payment_method", "customer"
    ).filter(created_by=user)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs.order_by("-created_at")


@employee_required
def employee_recent_sales(request):
    """Listing of the current employee's sales with edit/delete buttons
    for entries management has not yet acted on.

    Defaults to today; an optional collapsible date-range filter lets
    the employee scope the listing to any other day or window."""
    today = timezone.localdate()
    filter_form = EmployeeRecentFilterForm(request.GET or None)

    date_from = today
    date_to = today
    if filter_form.is_bound and filter_form.is_valid():
        date_from = filter_form.cleaned_data.get("date_from") or None
        date_to = filter_form.cleaned_data.get("date_to") or None
        # User explicitly opened the filter but left both boxes blank →
        # fall back to today so we never spam them with the full history.
        if date_from is None and date_to is None:
            date_from = date_to = today

    sales = list(_own_sales_qs(request.user, date_from=date_from, date_to=date_to))

    if date_from and date_to and date_from == date_to:
        scope_label = (
            _("Today") if date_from == today else date_from.strftime("%Y-%m-%d")
        )
    elif date_from and date_to:
        scope_label = _("%(start)s → %(end)s") % {
            "start": date_from.strftime("%Y-%m-%d"),
            "end": date_to.strftime("%Y-%m-%d"),
        }
    elif date_from:
        scope_label = _("From %(start)s") % {"start": date_from.strftime("%Y-%m-%d")}
    elif date_to:
        scope_label = _("Up to %(end)s") % {"end": date_to.strftime("%Y-%m-%d")}
    else:
        scope_label = _("All entries")

    return render(
        request,
        "sales/employee_recent.html",
        {
            "title": _("My entries"),
            "sales": sales,
            "filter_form": filter_form,
            "scope_label": scope_label,
            "is_default_today": (
                date_from == today and date_to == today and not request.GET
            ),
        },
    )


@employee_required
def employee_sale_edit(request, pk):
    """Employee edits one of their own sales — only while it's still in
    its initial state (see ``Sale.is_employee_modifiable``)."""
    sale = get_object_or_404(
        Sale.objects.select_related(
            "company", "product", "product__line", "payment_method"
        ),
        pk=pk,
        created_by=request.user,
    )
    if not sale.is_employee_modifiable:
        messages.error(
            request,
            _("This entry has already been processed by management and can no longer be changed."),
        )
        return redirect("sales:employee_recent_sales")

    if request.method == "POST":
        form = ManagementSaleEditForm(request.POST, instance=sale)
        if form.is_valid():
            try:
                update_sale_fields(
                    sale=sale,
                    payment_method=form.cleaned_data["payment_method"],
                    payer_name=form.cleaned_data["payer_name"],
                    reference_number=form.cleaned_data["reference_number"],
                    sell_price_actual=form.cleaned_data["sell_price_actual"],
                    notes=form.cleaned_data.get("notes") or "",
                    user=request.user,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Sale updated."))
                return redirect("sales:employee_recent_sales")
    else:
        form = ManagementSaleEditForm(instance=sale)

    return render(
        request,
        "sales/employee_sale_edit.html",
        {
            "form": form,
            "sale": sale,
            "title": _("Edit entry"),
        },
    )


@employee_required
@require_POST
def employee_sale_delete(request, pk):
    """Employee removes one of their own sales — same modifiability rule."""
    sale = get_object_or_404(Sale, pk=pk, created_by=request.user)
    htmx = is_htmx(request)
    if not sale.is_employee_modifiable:
        msg = _("This entry has already been processed by management and can no longer be deleted.")
        if htmx:
            return htmx_action_error(str(msg))
        messages.error(request, msg)
        return redirect("sales:employee_recent_sales")
    try:
        delete_sale_permanently(sale=sale)
    except (IntegrityError, DatabaseError) as exc:
        msg = _("Could not delete this entry: %(reason)s") % {"reason": str(exc)}
        if htmx:
            return htmx_action_error(str(msg))
        messages.error(request, msg)
        return redirect("sales:employee_recent_sales")
    if htmx:
        return htmx_remove_target()
    messages.success(request, _("Entry was permanently removed."))
    return redirect("sales:employee_recent_sales")


@employee_required
def employee_product_fragment(request):
    company_id = request.GET.get("company")
    if not company_id:
        return HttpResponseBadRequest()
    return render(
        request,
        "sales/partials/employee_product_tiles.html",
        {
            "product_groups": _build_product_groups(company_id),
            "product_errors": None,
            "selected_line_id": "",
        },
    )
