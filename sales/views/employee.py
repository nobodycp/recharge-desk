"""Employee-facing sales views: entry form, JSON helpers, product fragment."""

from django.contrib import messages
from django.db.models import Prefetch
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from accounts.permissions import employee_required, is_employee
from companies.models import Company, Product, ProductLine
from customers.services import resolve_or_create_customer_for_sale
from sales.forms import EmployeeSaleForm
from sales.models import PaymentMethod, Sale
from sales.payer_lookup import latest_sale_for_reference, payer_name_suggestions
from sales.pricing import ESIM_EXTRA_COST
from sales.services import create_sale


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
