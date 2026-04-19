import json

from django.contrib import messages
from django.db import DatabaseError, IntegrityError
from django.db.models import Prefetch, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _htmx_remove_target() -> HttpResponse:
    """Empty 200 body so htmx swaps an empty string into the target (removing it).

    NOTE: We deliberately avoid 204 here — htmx skips the swap on 204 by default,
    which would leave the row visually stuck even though the backend succeeded.
    """
    return HttpResponse("", status=200)


def _htmx_action_error(message: str, status: int = 200) -> HttpResponse:
    """Tell htmx to leave the row in place and surface the error to the user.

    Status 200 with HX-Reswap=none keeps the target untouched while still
    delivering the HX-Trigger event for the toast/alert. We do not use a
    non-2xx code because htmx treats those as transport errors and may
    suppress the trigger depending on configuration.
    """
    resp = HttpResponse("", status=status)
    resp["HX-Reswap"] = "none"
    resp["HX-Trigger"] = json.dumps({"rdSaleActionError": message or "Action failed"})
    return resp

from accounts.permissions import employee_required, is_employee, management_required
from core.pagination import paginate_request
from companies.models import Company, Product, ProductLine
from sales.forms import (
    EmployeeSaleForm,
    ManagementSaleEditForm,
    ManagementSaleFilterForm,
    PaymentMethodForm,
)
from sales.models import CompanyBalanceTransaction, PaymentMethod, Sale
from sales.query_utils import (
    apply_management_sale_filter_data,
    apply_sale_list_ordering,
)
from sales.payer_lookup import latest_sale_for_reference, payer_name_suggestions
from sales.pricing import ESIM_EXTRA_COST
from customers.services import approve_sale as approve_on_account_sale
from customers.services import reject_sale as reject_on_account_sale
from customers.services import resolve_or_create_customer_for_sale
from sales.services import (
    cancel_sale,
    create_sale,
    delete_sale_permanently,
    mark_sale_paid,
    update_sale_fields,
)

@employee_required
def employee_entry(request):
    company_id = request.POST.get("company") or request.GET.get("company")
    initial = {}
    if request.method == "GET" and company_id:
        initial["company"] = company_id
    form = EmployeeSaleForm(request.POST or None, company_id=company_id, initial=initial)
    companies = list(Company.objects.filter(is_active=True).order_by("name"))
    payment_methods = list(PaymentMethod.objects.filter(is_active=True).order_by("name"))
    product_groups = []
    if company_id:
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
        for line in lines_qs:
            variants = list(line.variants.all())
            if variants:
                variant_ids = {p.pk for p in variants}
                default_product_id = ""
                if line.default_package_id and line.default_package_id in variant_ids:
                    default_product_id = str(line.default_package_id)
                product_groups.append(
                    {"line": line, "variants": variants, "default_product_id": default_product_id}
                )
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
    """
    JSON: latest sale snapshot (payer name + company/product hints) for an
    exact phone/shipment (reference_number) match.
    """
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
    product_groups = []
    for line in lines_qs:
        variants = list(line.variants.all())
        if variants:
            variant_ids = {p.pk for p in variants}
            default_product_id = ""
            if line.default_package_id and line.default_package_id in variant_ids:
                default_product_id = str(line.default_package_id)
            product_groups.append(
                {"line": line, "variants": variants, "default_product_id": default_product_id}
            )
    return render(
        request,
        "sales/partials/employee_product_tiles.html",
        {
            "product_groups": product_groups,
            "product_errors": None,
            "selected_line_id": "",
        },
    )


@management_required
def management_sale_list(request):
    qs = Sale.objects.select_related(
        "company",
        "product",
        "product__line",
        "payment_method",
        "created_by",
    ).order_by("-created_at")
    form = ManagementSaleFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = apply_management_sale_filter_data(qs, data)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(reference_number__icontains=q)
            | Q(payer_name__icontains=q)
            | Q(product__line__name__icontains=q)
            | Q(company__name__icontains=q)
        )
    qs = apply_sale_list_ordering(request, qs)
    page_obj = paginate_request(request, qs)
    ctx = {
        "page_obj": page_obj,
        "filter_form": form,
        "title": _("Sales"),
        "sort": request.GET.get("sort") or "created_at",
        "order": (request.GET.get("order") or "desc").lower(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "sales/partials/management_sale_list_results.html", ctx)
    return render(request, "sales/management_sale_list.html", ctx)


@management_required
def pending_payments(request):
    """
    Real-money pending sales only — the till expects cash/transfer to land.
    Approved on-account sales also live in PENDING but are settled via the
    customer's account (FIFO on the next CustomerPayment), so they belong
    on the customer detail page, not here.
    """
    qs = (
        Sale.objects.filter(status=Sale.Status.PENDING, on_account=False)
        .select_related("company", "product", "product__line", "payment_method", "created_by")
    )
    form = ManagementSaleFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = apply_management_sale_filter_data(qs, data, omit_status=True)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(reference_number__icontains=q)
            | Q(payer_name__icontains=q)
            | Q(product__line__name__icontains=q)
            | Q(company__name__icontains=q)
        )
    qs = apply_sale_list_ordering(request, qs)
    page_obj = paginate_request(request, qs)
    ctx = {
        "page_obj": page_obj,
        "filter_form": form,
        "title": _("Pending payments"),
        "sort": request.GET.get("sort") or "created_at",
        "order": (request.GET.get("order") or "desc").lower(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "sales/partials/pending_payments_results.html", ctx)
    return render(request, "sales/pending_payments.html", ctx)


@management_required
def awaiting_approvals(request):
    """List on-account sales waiting for management approval."""
    qs = (
        Sale.objects.filter(status=Sale.Status.AWAITING)
        .select_related("company", "product", "product__line", "customer", "created_by")
        .order_by("-created_at")
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(reference_number__icontains=q)
            | Q(payer_name__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(company__name__icontains=q)
        )
    page_obj = paginate_request(request, qs)
    ctx = {
        "page_obj": page_obj,
        "title": _("Awaiting approval"),
        "q": q,
    }
    if request.headers.get("HX-Request"):
        return render(request, "sales/partials/awaiting_results.html", ctx)
    return render(request, "sales/awaiting_approvals.html", ctx)


@management_required
def sale_approve(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if request.method != "POST":
        return redirect("sales:awaiting_approvals")
    htmx = _is_htmx(request)
    try:
        approve_on_account_sale(sale=sale, user=request.user)
    except ValueError as exc:
        if htmx:
            return _htmx_action_error(str(exc))
        messages.error(request, str(exc))
        return redirect(request.META.get("HTTP_REFERER") or "sales:awaiting_approvals")
    if htmx:
        return _htmx_remove_target()
    messages.success(request, _("Sale approved and posted to the customer's account."))
    return redirect(request.META.get("HTTP_REFERER") or "sales:awaiting_approvals")


@management_required
def sale_reject(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if request.method != "POST":
        return redirect("sales:awaiting_approvals")
    htmx = _is_htmx(request)
    try:
        reject_on_account_sale(sale=sale, user=request.user)
    except ValueError as exc:
        if htmx:
            return _htmx_action_error(str(exc))
        messages.error(request, str(exc))
        return redirect(request.META.get("HTTP_REFERER") or "sales:awaiting_approvals")
    if htmx:
        return _htmx_remove_target()
    messages.success(request, _("Sale rejected and supplier balance restored."))
    return redirect(request.META.get("HTTP_REFERER") or "sales:awaiting_approvals")


@management_required
def sale_mark_paid(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if request.method != "POST":
        return redirect("sales:pending_payments")
    htmx = _is_htmx(request)
    try:
        mark_sale_paid(sale=sale, user=request.user)
    except ValueError as exc:
        if htmx:
            return _htmx_action_error(str(exc))
        messages.error(request, str(exc))
        return redirect(request.META.get("HTTP_REFERER") or "sales:pending_payments")
    if htmx:
        return _htmx_remove_target()
    messages.success(request, _("Marked as paid."))
    return redirect(request.META.get("HTTP_REFERER") or "sales:pending_payments")


@management_required
def sale_cancel(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if request.method != "POST":
        return redirect("sales:management_sale_list")
    htmx = _is_htmx(request)
    try:
        cancel_sale(sale=sale, user=request.user)
    except ValueError as exc:
        if htmx:
            return _htmx_action_error(str(exc))
        messages.error(request, str(exc))
        return redirect(request.META.get("HTTP_REFERER") or "sales:management_sale_list")
    if htmx:
        return _htmx_remove_target()
    messages.success(request, _("Sale cancelled and supplier balance restored."))
    return redirect(request.META.get("HTTP_REFERER") or "sales:management_sale_list")


@management_required
def sale_edit(request, pk):
    sale = get_object_or_404(
        Sale.objects.select_related("company", "product", "product__line", "payment_method"),
        pk=pk,
    )
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
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
                return redirect(next_url or "sales:management_sale_list")
    else:
        form = ManagementSaleEditForm(instance=sale)
    return render(
        request,
        "sales/sale_edit.html",
        {
            "form": form,
            "sale": sale,
            "next_url": next_url,
            "title": _("Edit sale"),
        },
    )


@management_required
def sale_delete_permanent(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if request.method != "POST":
        return redirect("sales:management_sale_list")
    htmx = _is_htmx(request)
    try:
        delete_sale_permanently(sale=sale)
    except (IntegrityError, DatabaseError) as exc:
        msg = _("Could not delete this sale: %(reason)s") % {"reason": str(exc)}
        if htmx:
            return _htmx_action_error(str(msg))
        messages.error(request, msg)
        return redirect(request.META.get("HTTP_REFERER") or "sales:management_sale_list")
    if htmx:
        return _htmx_remove_target()
    messages.success(request, _("Sale was permanently removed from the system."))
    return redirect(request.META.get("HTTP_REFERER") or "sales:management_sale_list")


@management_required
def payment_method_list(request):
    qs = PaymentMethod.objects.order_by("name")
    page_obj = paginate_request(request, qs)
    return render(
        request,
        "sales/payment_method_list.html",
        {"page_obj": page_obj, "title": _("Payment methods")},
    )


@management_required
def payment_method_create(request):
    form = PaymentMethodForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Payment method saved."))
        return redirect("sales:payment_method_list")
    return render(
        request,
        "sales/payment_method_form.html",
        {"form": form, "title": _("New payment method")},
    )


@management_required
def payment_method_edit(request, pk):
    obj = get_object_or_404(PaymentMethod, pk=pk)
    form = PaymentMethodForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Payment method updated."))
        return redirect("sales:payment_method_list")
    return render(
        request,
        "sales/payment_method_form.html",
        {"form": form, "title": _("Edit payment method")},
    )


@management_required
@require_POST
def bulk_sales_mark_paid(request):
    ids = []
    for x in request.POST.getlist("sale_ids"):
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    next_url = (request.POST.get("next") or "").strip() or reverse("sales:management_sale_list")
    if not ids:
        messages.warning(request, _("No sales selected."))
    else:
        qs = Sale.objects.filter(pk__in=ids, status=Sale.Status.PENDING)
        ok = 0
        failed = 0
        for sale in qs:
            try:
                mark_sale_paid(sale=sale, user=request.user)
                ok += 1
            except ValueError:
                failed += 1
        if ok:
            messages.success(request, _("Marked %(n)s sale(s) as paid.") % {"n": ok})
        if failed:
            messages.warning(
                request,
                _("%(n)s sale(s) could not be updated (already settled or on-account).")
                % {"n": failed},
            )
        if not ok and not failed:
            messages.info(request, _("No pending sales were updated."))
    if request.headers.get("HX-Request"):
        r = HttpResponse(status=204)
        r["HX-Redirect"] = next_url
        return r
    return redirect(next_url)


