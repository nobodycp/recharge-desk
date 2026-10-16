"""Employee-facing sales views: entry form, JSON helpers, product fragment."""

import uuid

from django.contrib import messages
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Prefetch
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import employee_required, is_management
from companies.models import Company, Product, ProductLine
from core.http_utils import get_client_ip
from core.models import AppSettings
from core.sale_workflow import finalize_payment_submission_after_entry, finalize_sale_after_entry
from customers.forms import EmployeeCustomerPaymentSubmissionForm
from customers.models import CustomerPaymentSubmission
from customers.services import (
    delete_employee_customer_payment_submission,
    resolve_or_create_customer_for_sale,
    submit_customer_payment_submission,
    update_awaiting_customer_payment_submission,
)
from customers.views._shared import flash_form_errors
from phone_refresh.models import RefreshSource, SystemSettings
from phone_refresh.services.refresh_jobs import job_payload, submit_refresh
from sales.forms import EmployeeRecentFilterForm, EmployeeSaleEditForm, EmployeeSaleForm
from sales.models import PaymentMethod, Sale
from sales.number_check import check_payload, submit_number_check
from sales.employee_editing import (
    EMPLOYEE_RECENT_EDIT_LIMIT,
    employee_editable_payment_submission_ids,
    employee_editable_sale_ids,
    payment_submission_is_employee_deletable,
    payment_submission_is_employee_editable,
    sale_is_employee_editable,
)
from sales.charging import (
    DUPLICATE_SUBMIT,
    REPEAT_CHARGE_MESSAGE,
    can_charge,
    sale_recorded_today,
    user_may_charge,
    create_charged_sale,
    find_duplicate_submit,
)
from sales.phone_validation import SALE_PHONE_RE, normalize_sale_phone
from sales.payer_lookup import latest_sale_for_reference, payer_name_suggestions
from sales.pricing import ESIM_EXTRA_COST
from inventory.forms import CustomerStockFilterForm
from inventory.models import SimStockBalance
from inventory.services import preview_sim_stock_deduction
from employees.services import get_acting_employee_profile
from sales.services import create_sale, delete_sale_permanently, update_sale_fields
from sales.views._shared import (
    htmx_action_error,
    htmx_remove_target,
    is_htmx,
    remaining_recheck_polls,
    render_sale_row,
    sale_row_layout,
    sale_row_queryset,
)


def _line_groups(lines):
    groups = []
    for line in lines:
        variants = list(line.variants.all())
        if not variants:
            continue
        variant_ids = {p.pk for p in variants}
        default_product_id = ""
        if line.default_package_id and line.default_package_id in variant_ids:
            default_product_id = str(line.default_package_id)
        groups.append(
            {"line": line, "variants": variants, "default_product_id": default_product_id}
        )
    return groups


def _product_line_queryset(company_ids):
    return (
        ProductLine.objects.filter(company_id__in=list(company_ids), is_active=True)
        .select_related("default_package")
        .prefetch_related(
            Prefetch(
                "variants",
                queryset=Product.objects.filter(is_active=True).select_related("line"),
            )
        )
        .order_by("sort_order", "name")
    )


def _build_product_groups(company_id):
    if not company_id:
        return []
    return _line_groups(_product_line_queryset([company_id]))


def _company_product_bundles(companies):
    """Every active company's lines, loaded once with the sale form."""
    by_company = {c.pk: [] for c in companies}
    for line in _product_line_queryset(by_company):
        by_company.setdefault(line.company_id, []).append(line)
    return [
        {"company_id": c.pk, "groups": _line_groups(by_company.get(c.pk, []))}
        for c in companies
    ]


def _duplicate_charge_redirect(request, sale):
    messages.info(
        request,
        _("This sale was already recorded (%(status)s). No new charge was made.")
        % {"status": sale.get_charge_status_display()},
    )
    return redirect("sales:employee_entry")


@employee_required
def employee_entry(request):
    company_id = request.POST.get("company") or request.GET.get("company")
    initial = {}
    from_reconcile = False
    if request.method == "GET":
        from sales.charging import notify_discarded_charges

        notify_discarded_charges(request)
        if company_id:
            initial["company"] = company_id
        # Quick-record link from the supplier reconcile page: prefill a valid number only.
        phone = normalize_sale_phone(request.GET.get("phone", ""))
        if SALE_PHONE_RE.match(phone):
            initial["reference_number"] = phone
        from_reconcile = request.GET.get("from") == "reconcile"
    form = EmployeeSaleForm(
        request.POST if request.method == "POST" else None,
        company_id=company_id,
        initial=initial,
        user=request.user,
    )
    companies = list(Company.objects.filter(is_active=True).order_by("name"))
    may_charge = user_may_charge(request.user)
    chargeable_company_ids = {c.pk for c in companies if may_charge and can_charge(c)}
    payment_methods = list(PaymentMethod.objects.filter(is_active=True).order_by("name"))
    acting_employee = get_acting_employee_profile(request.user)
    company_product_bundles = _company_product_bundles(companies)
    charge_requested = request.POST.get("action") == "charge"

    if request.method == "POST" and form.is_valid():
        try:
            if charge_requested:
                duplicate = find_duplicate_submit(request.POST.get("form_token", ""), request.user)
                if duplicate is not None:
                    return _duplicate_charge_redirect(request, duplicate)
                if not user_may_charge(request.user) or not can_charge(form.cleaned_data["company"]):
                    raise ValueError(_("This company cannot be charged through its provider."))
            if (
                sale_recorded_today(form.cleaned_data["reference_number"])
                and request.POST.get("repeat_charge") != "1"
            ):
                raise ValueError(REPEAT_CHARGE_MESSAGE)
            on_account = bool(form.cleaned_data.get("on_account"))
            app_settings = AppSettings.load()
            paid_via_employee = (
                bool(form.cleaned_data.get("paid_via_employee"))
                and app_settings.sales_show_employee_payment
            )
            customer = None
            if on_account:
                customer = resolve_or_create_customer_for_sale(
                    name=form.cleaned_data["payer_name"],
                    phone=form.cleaned_data["reference_number"],
                    user=request.user,
                )
            employee_recipient = (
                form.cleaned_data.get("employee_recipient") if paid_via_employee else None
            )
            is_new_sim = bool(form.cleaned_data.get("is_new_sim")) and app_settings.sales_inventory_enabled
            sale_kwargs = dict(
                company=form.cleaned_data["company"],
                product=form.cleaned_data["product"],
                reference_number=form.cleaned_data["reference_number"],
                payer_name=form.cleaned_data["payer_name"],
                payment_method=form.cleaned_data["payment_method"]
                if not on_account and not paid_via_employee
                else None,
                sell_price_actual=form.cleaned_data["sell_price_actual"],
                notes=form.cleaned_data.get("notes") or "",
                is_esim=bool(form.cleaned_data.get("is_esim")),
                is_new_sim=is_new_sim,
                sim_serial_or_iccid=form.cleaned_data.get("sim_serial_or_iccid") or "",
                duration_days=form.cleaned_data.get("duration_days"),
                on_account=on_account,
                customer=customer,
                paid_via_employee=paid_via_employee,
                employee_recipient=employee_recipient,
            )
            if charge_requested:
                sale, outcome = create_charged_sale(
                    form_token=request.POST.get("form_token", ""),
                    user=request.user,
                    **sale_kwargs,
                )
            else:
                with transaction.atomic():
                    sale = create_sale(user=request.user, **sale_kwargs)
                    outcome = finalize_sale_after_entry(
                        sale=sale,
                        user=request.user,
                        on_account=on_account,
                        paid_via_employee=paid_via_employee,
                    )
            if outcome == DUPLICATE_SUBMIT:
                return _duplicate_charge_redirect(request, sale)
            charge_notice = ""
            if charge_requested:
                try:
                    sale.refresh_from_db()
                except Sale.DoesNotExist:
                    charge_notice = "discarded"
                else:
                    charge_notice = (
                        "failed" if sale.charge_status == Sale.ChargeStatus.FAILED else "pending"
                    )
            if charge_notice == "discarded":
                messages.error(
                    request,
                    _(
                        "Charge for %(phone)s never reached the provider. "
                        "The sale was not recorded. Record it manually."
                    )
                    % {"phone": form.cleaned_data["reference_number"]},
                )
            elif charge_notice == "failed":
                messages.error(
                    request,
                    sale.charge_message or _("Charge failed"),
                )
            else:
                if outcome == "posted_debt":
                    messages.success(request, _("Recorded on account and posted to the customer."))
                elif outcome in ("paid", "paid_employee"):
                    if paid_via_employee:
                        messages.success(
                            request,
                            _("Sale recorded; payment credited to employee ledger."),
                        )
                    else:
                        messages.success(request, _("Sale recorded and marked paid."))
                elif on_account:
                    messages.success(
                        request, _("Recorded as on-account; awaiting management approval.")
                    )
                else:
                    messages.success(request, _("Sale recorded successfully."))
                if charge_notice == "pending":
                    messages.info(
                        request,
                        _("Charge sent to the provider. Follow its status in My entries."),
                    )
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
            "from_reconcile": from_reconcile,
            "company_product_bundles": company_product_bundles,
            "companies": companies,
            "payment_methods": payment_methods,
            "acting_employee": acting_employee,
            "selected_company_id": str(company_id or ""),
            "selected_line_id": selected_line_id,
            "selected_product_id": selected_product_id,
            "selected_payment_id": str(request.POST.get("payment_method", "") or ""),
            "esim_extra": ESIM_EXTRA_COST,
            "form_token": str(uuid.uuid4()),
            "chargeable_company_ids": chargeable_company_ids,
            "charge_available": str(company_id or "")
            in {str(pk) for pk in chargeable_company_ids},
        },
    )


@employee_required
def employee_refresh_number(request):
    """Full-page number check («فحص الأرقام»): refresh or check a number."""
    if not AppSettings.load().sales_show_refresh_phone:
        messages.error(request, _("Number refresh is disabled on the sales screen."))
        return redirect("sales:employee_entry")
    return render(
        request,
        "sales/employee_refresh_number.html",
        {
            "title": "فحص الأرقام",
            "refresh_wait_seconds": SystemSettings.get().refresh_wait_seconds,
        },
    )


@employee_required
def employee_record_payment(request):
    """Full-page customer payment entry for cashiers."""
    if not AppSettings.load().sales_show_record_payment:
        messages.error(request, _("Recording payments from the sales screen is disabled."))
        return redirect("sales:employee_entry")
    payment_methods = list(PaymentMethod.objects.filter(is_active=True).order_by("name"))
    today = timezone.localdate()
    recent_payments = list(
        CustomerPaymentSubmission.objects.filter(
            created_by=request.user,
            created_at__date=today,
        )
        .select_related("customer", "payment_method")
        .order_by("-created_at", "-id")[:20]
    )
    editable_payment_ids = employee_editable_payment_submission_ids(request.user)
    return render(
        request,
        "sales/employee_record_payment.html",
        {
            "title": _("Record payment"),
            "pay_sub_form": EmployeeCustomerPaymentSubmissionForm(user=request.user),
            "payment_methods": payment_methods,
            "acting_employee": get_acting_employee_profile(request.user),
            "recent_payments": recent_payments,
            "editable_payment_ids": editable_payment_ids,
        },
    )


@employee_required
@require_POST
def employee_submit_customer_payment_submission(request):
    app_settings = AppSettings.load()
    if not app_settings.sales_show_record_payment:
        messages.error(request, _("Recording payments from the sales screen is disabled."))
        return redirect("sales:employee_entry")
    form = EmployeeCustomerPaymentSubmissionForm(request.POST, user=request.user)
    if not form.is_valid():
        flash_form_errors(request, form)
        return redirect("sales:employee_record_payment")
    paid_via_employee = (
        bool(form.cleaned_data.get("paid_via_employee"))
        and app_settings.sales_show_employee_payment
    )
    try:
        submission = submit_customer_payment_submission(
            customer=form.cleaned_data["customer"],
            amount=form.cleaned_data["amount"],
            payment_method=form.cleaned_data.get("payment_method"),
            notes=form.cleaned_data.get("notes") or "",
            user=request.user,
            paid_via_employee=paid_via_employee,
            employee_recipient=form.cleaned_data.get("employee_recipient")
            if paid_via_employee
            else None,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        if finalize_payment_submission_after_entry(submission=submission, user=request.user):
            if paid_via_employee:
                messages.success(
                    request,
                    _("Payment recorded on the customer account and credited to employee ledger."),
                )
            else:
                messages.success(request, _("Payment recorded on the customer account."))
        else:
            messages.success(request, _("Payment submitted for management approval."))
    return redirect("sales:employee_record_payment")


@employee_required
@never_cache
@require_GET
def employee_sale_today(request):
    """Whether this number already has a sale recorded today (local date)."""
    phone = normalize_sale_phone(request.GET.get("phone", ""))
    return JsonResponse(
        {
            "charged_today": sale_recorded_today(phone),
            "message": REPEAT_CHARGE_MESSAGE,
        }
    )


@employee_required
@require_POST
def employee_refresh_phone(request):
    """Staff refresh — same queued flow as the public site, without API rate limits."""
    if not AppSettings.load().sales_show_refresh_phone:
        return JsonResponse(
            {
                "status": "error",
                "message": {
                    "title": str(_("Error")),
                    "body": str(_("Number refresh is disabled on the sales screen.")),
                },
            },
            status=403,
        )
    phone = (request.POST.get("phone") or "").strip()
    ticket = submit_refresh(
        phone,
        ip=get_client_ip(request) or None,
        source=RefreshSource.EMPLOYEE,
    )
    if ticket.job_id is None:
        # Answered without a job (validation, cooldown, service off, busy).
        return JsonResponse(ticket.payload)
    return JsonResponse({"status": "processing", "job": ticket.job_id}, status=202)


@employee_required
@never_cache
@require_GET
def employee_refresh_status(request, job_id):
    """Poll a refresh job started by :func:`employee_refresh_phone`.

    Returns exactly what :func:`job_payload` returns (customer-safe).
    """
    return JsonResponse(job_payload(str(job_id)))


def _number_check_disabled_response():
    """403 JSON when the number tools are switched off on the sales screen."""
    return JsonResponse(
        {
            "status": "error",
            "message": str(_("Number refresh is disabled on the sales screen.")),
        },
        status=403,
    )


@employee_required
@require_POST
def employee_check_phone(request):
    """Start a number check («فحص الرقم»): live provider data plus our sale price."""
    if not AppSettings.load().sales_show_refresh_phone:
        return _number_check_disabled_response()
    ticket = submit_number_check(request.POST.get("phone") or "", user=request.user)
    if ticket.job_id is None:
        # Answered without a job (invalid, no sale, unsupported, gate, busy).
        return JsonResponse(ticket.payload)
    return JsonResponse({"status": "processing", "job": ticket.job_id}, status=202)


@employee_required
@never_cache
@require_GET
def employee_check_status(request, job_id):
    """Poll a check started by :func:`employee_check_phone` (card HTML or a message)."""
    if not AppSettings.load().sales_show_refresh_phone:
        return _number_check_disabled_response()
    return JsonResponse(check_payload(str(job_id)))


@employee_required
@never_cache
@require_GET
def sale_charge_row(request, pk):
    """One sale's row, re-fetched every few seconds while its charge is processing.

    Employees get their own "My entries" row only; management may fetch any
    sale, including the management list row/card (``?layout=row|card``), and
    keeps polling an open charge for a while after a recheck (``?polls=``).
    """
    management = is_management(request.user)
    qs = sale_row_queryset()
    if not management:
        qs = qs.filter(created_by=request.user)
    sale = get_object_or_404(qs, pk=pk)
    layout = sale_row_layout(request, management=management)
    return render_sale_row(
        request, sale, layout, recheck_polls=remaining_recheck_polls(request, sale)
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
def api_sim_stock_preview(request):
    """JSON: estimate SIM stock source for New SIM (no mutation)."""
    if not AppSettings.load().sales_inventory_enabled:
        return JsonResponse({"ok": False, "message": str(_("Inventory is disabled on the sales screen."))})
    payer = (request.GET.get("payer") or "").strip()
    product_id = (request.GET.get("product") or "").strip()
    if not payer or not product_id:
        return JsonResponse({"ok": False, "message": str(_("Payer name and product are required."))})
    try:
        product = Product.objects.select_related("line", "line__company").get(
            pk=int(product_id), is_active=True
        )
    except (ValueError, Product.DoesNotExist):
        return JsonResponse({"ok": False, "message": str(_("Product not found."))})
    payload = preview_sim_stock_deduction(payer_name=payer, product_line=product.line)
    payload["ok"] = True
    return JsonResponse(payload)


@employee_required
@require_GET
def api_payer_name_suggestions(request):
    """JSON: distinct historical payer names matching q (for autocomplete)."""
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"suggestions": []})
    items = payer_name_suggestions(q, limit=10)
    return JsonResponse(
        {
            "suggestions": [
                {
                    "name": x["name"],
                    "count": x["count"],
                    "is_customer_account": x["is_customer_account"],
                }
                for x in items
            ]
        }
    )


def _own_sales_qs(user, *, cleaned_filters=None, default_date=None):
    """Build the listing queryset for the employee's "My entries" page.

    Always restricted to ``created_by=user`` so an employee never sees
    another cashier's rows. ``cleaned_filters`` is the validated
    ``cleaned_data`` of :class:`EmployeeRecentFilterForm`; when no date
    bounds are supplied we fall back to ``default_date`` (typically
    today) so the page doesn't dump the entire history.
    """
    from django.db.models import Q

    qs = Sale.objects.select_related(
        "company", "product", "product__line", "payment_method", "customer"
    ).filter(created_by=user)

    data = cleaned_filters or {}
    q = (data.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference_number__icontains=q) | Q(payer_name__icontains=q))
    if data.get("company"):
        qs = qs.filter(company=data["company"])
    if data.get("payment_method"):
        qs = qs.filter(payment_method=data["payment_method"])
    status = data.get("status") or ""
    if status.startswith("pay_"):
        qs = qs.none()
    elif status:
        qs = qs.filter(status=status)

    date_from = data.get("date_from")
    date_to = data.get("date_to")
    if not date_from and not date_to and default_date is not None:
        date_from = date_to = default_date
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    return qs.order_by("-created_at"), date_from, date_to


def _own_payment_submissions_qs(user, *, cleaned_filters=None, default_date=None):
    """Payment submissions created by this employee for My entries."""
    from django.db.models import Q

    qs = CustomerPaymentSubmission.objects.select_related(
        "customer", "payment_method", "resulting_payment"
    ).filter(created_by=user)

    data = cleaned_filters or {}
    # Company is sale-only — skip payments when that filter is active.
    if data.get("company"):
        return qs.none(), data.get("date_from"), data.get("date_to")

    q = (data.get("q") or "").strip()
    if q:
        qs = qs.filter(customer__name__icontains=q)
    if data.get("payment_method"):
        qs = qs.filter(payment_method=data["payment_method"])
    status = data.get("status") or ""
    if status.startswith("pay_"):
        qs = qs.filter(status=status.removeprefix("pay_"))
    elif status:
        qs = qs.none()

    date_from = data.get("date_from")
    date_to = data.get("date_to")
    if not date_from and not date_to and default_date is not None:
        date_from = date_to = default_date
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    return qs.order_by("-created_at"), date_from, date_to


def _merge_employee_entries(sales, payments):
    """Newest-first mix of sales and payment submissions for My entries."""
    entries = []
    for s in sales:
        entries.append(
            {
                "kind": "sale",
                "created_at": s.created_at,
                "sort_id": s.pk,
                "sale": s,
            }
        )
    for p in payments:
        entries.append(
            {
                "kind": "payment",
                "created_at": p.created_at,
                "sort_id": p.pk,
                "payment": p,
            }
        )
    entries.sort(key=lambda e: (e["created_at"], e["sort_id"]), reverse=True)
    return entries


def _stock_check_customer_groups(balances):
    """One card per customer with all product-line quantities."""
    groups: list[dict] = []
    current: dict | None = None
    for row in balances:
        if current is None or current["customer"].pk != row.customer_id:
            current = {
                "customer": row.customer,
                "lines": [],
                "total": 0,
            }
            groups.append(current)
        current["lines"].append(
            {"name": row.product_line.name, "quantity": row.quantity}
        )
        current["total"] += int(row.quantity or 0)
    return groups


@employee_required
def employee_stock_check(request):
    """Cashier-facing SIM stock lookup by customer name."""
    qs = (
        SimStockBalance.objects.filter(
            location=SimStockBalance.Location.CUSTOMER,
            quantity__gt=0,
        )
        .select_related("customer", "product_line")
        .order_by("customer__name", "product_line__name")
    )
    form = CustomerStockFilterForm(request.GET or None)
    q = ""
    if form.is_valid():
        q = (form.cleaned_data.get("q") or "").strip()
        if q:
            qs = qs.filter(customer__name__icontains=q)
        if form.cleaned_data.get("product_line"):
            qs = qs.filter(product_line=form.cleaned_data["product_line"])
    # Cap rows so live typing stays snappy; filter narrows further via ``q``.
    balances = list(qs[:400])
    customer_groups = _stock_check_customer_groups(balances)
    ctx = {
        "title": _("Check stock"),
        "filter_form": form,
        "customer_groups": customer_groups,
        "q": q,
    }
    # Live filter targets the results fragment. Tabbar/wallet hx-boost also
    # sends HX-Request, but with HX-Target=body — that must get the full page.
    if (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-Target") == "rd-stock-check-results"
    ):
        return render(
            request,
            "sales/partials/employee_stock_check_results.html",
            ctx,
        )
    return render(request, "sales/employee_stock_check.html", ctx)


@employee_required
def employee_recent_sales(request):
    """Listing of the current employee's sales and payment submissions.

    Defaults to today; an optional collapsible filter card lets the
    employee narrow by free-text (number/name), company, payment
    method, status, and date range — any subset, in any combination."""
    from sales.charging import notify_discarded_charges

    notify_discarded_charges(request)
    today = timezone.localdate()
    filter_form = EmployeeRecentFilterForm(request.GET or None)

    # Decide what "default scope" means. With no bound filters, or a
    # bound filter that submitted nothing useful, we lock to today so
    # the page never fires an unbounded query.
    default_date = today
    cleaned = None
    if filter_form.is_bound and filter_form.is_valid():
        cleaned = filter_form.cleaned_data
        # If the user touched any non-date filter, honour their explicit
        # submission and stop forcing today as a date boundary.
        non_date_active = any(
            cleaned.get(name)
            for name in filter_form.fields
            if name not in {"date_from", "date_to"}
        )
        if non_date_active or cleaned.get("date_from") or cleaned.get("date_to"):
            default_date = None

    sales_qs, date_from, date_to = _own_sales_qs(
        request.user, cleaned_filters=cleaned, default_date=default_date
    )
    payments_qs, _pay_from, _pay_to = _own_payment_submissions_qs(
        request.user, cleaned_filters=cleaned, default_date=default_date
    )
    del _pay_from, _pay_to
    entries = _merge_employee_entries(list(sales_qs), list(payments_qs))
    editable_sale_ids = employee_editable_sale_ids(request.user)
    editable_payment_ids = employee_editable_payment_submission_ids(request.user)

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

    is_default_today = (
        date_from == today and date_to == today and not request.GET
    )

    return render(
        request,
        "sales/employee_recent.html",
        {
            "title": _("My entries"),
            "entries": entries,
            "editable_sale_ids": editable_sale_ids,
            "editable_payment_ids": editable_payment_ids,
            "filter_form": filter_form,
            "scope_label": scope_label,
            "is_default_today": is_default_today,
        },
    )


def _not_employee_editable_message(sale: Sale) -> str:
    if sale.entry_mode == Sale.EntryMode.AUTO_CHARGE:
        return _(
            "Sales charged through the provider can't be edited or deleted by "
            "employees. Ask management."
        )
    return _("You can only edit or delete your last %(count)s entries.") % {
        "count": EMPLOYEE_RECENT_EDIT_LIMIT
    }


@employee_required
def employee_sale_edit(request, pk):
    """Employee edits one of their own sales — only while it's still in
    its initial state (see ``Sale.is_employee_modifiable``)."""
    sale = get_object_or_404(
        Sale.objects.select_related(
            "company", "product", "product__line", "payment_method", "employee_recipient"
        ),
        pk=pk,
        created_by=request.user,
    )
    if not sale_is_employee_editable(sale, request.user):
        messages.error(request, _not_employee_editable_message(sale))
        return redirect("sales:employee_recent_sales")

    if request.method == "POST":
        form = EmployeeSaleEditForm(request.POST, instance=sale)
        if form.is_valid():
            try:
                update_sale_fields(
                    sale=sale,
                    payment_method=form.cleaned_data.get("payment_method"),
                    payer_name=form.cleaned_data["payer_name"],
                    reference_number=form.cleaned_data["reference_number"],
                    sell_price_actual=form.cleaned_data["sell_price_actual"],
                    notes=form.cleaned_data.get("notes") or "",
                    user=request.user,
                )
                if sale.paid_via_employee:
                    from employees.services import sync_sales_payment_ledger_for_sale

                    sale.refresh_from_db()
                    sync_sales_payment_ledger_for_sale(sale=sale, user=request.user)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Sale updated."))
                return redirect("sales:employee_recent_sales")
    else:
        form = EmployeeSaleEditForm(instance=sale)

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
    if not sale_is_employee_editable(sale, request.user):
        msg = _not_employee_editable_message(sale)
        if htmx:
            return htmx_action_error(str(msg))
        messages.error(request, msg)
        return redirect("sales:employee_recent_sales")
    try:
        delete_sale_permanently(sale=sale, user=request.user)
    except (ValueError, IntegrityError, DatabaseError) as exc:
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
def employee_payment_edit(request, pk):
    """Edit an awaiting customer payment submission the employee created."""
    if not AppSettings.load().sales_show_record_payment:
        messages.error(request, _("Recording payments from the sales screen is disabled."))
        return redirect("sales:employee_entry")
    submission = get_object_or_404(
        CustomerPaymentSubmission.objects.select_related(
            "customer", "payment_method", "employee_recipient"
        ),
        pk=pk,
        created_by=request.user,
    )
    if not payment_submission_is_employee_editable(submission, request.user):
        messages.error(
            request,
            _("You can only edit awaiting payments among your last %(count)s payments.")
            % {"count": EMPLOYEE_RECENT_EDIT_LIMIT},
        )
        return redirect("sales:employee_recent_sales")

    payment_methods = list(PaymentMethod.objects.filter(is_active=True).order_by("name"))
    acting_employee = get_acting_employee_profile(request.user)

    if request.method == "POST":
        form = EmployeeCustomerPaymentSubmissionForm(request.POST, user=request.user)
        if form.is_valid():
            paid_via_employee = (
                bool(form.cleaned_data.get("paid_via_employee"))
                and AppSettings.load().sales_show_employee_payment
            )
            try:
                update_awaiting_customer_payment_submission(
                    submission=submission,
                    customer=form.cleaned_data["customer"],
                    amount=form.cleaned_data["amount"],
                    payment_method=form.cleaned_data.get("payment_method"),
                    notes=form.cleaned_data.get("notes") or "",
                    user=request.user,
                    paid_via_employee=paid_via_employee,
                    employee_recipient=form.cleaned_data.get("employee_recipient")
                    if paid_via_employee
                    else None,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Payment entry updated."))
                return redirect("sales:employee_recent_sales")
        else:
            flash_form_errors(request, form)
    else:
        form = EmployeeCustomerPaymentSubmissionForm(
            user=request.user,
            initial={
                "customer": submission.customer_id,
                "amount": submission.amount,
                "payment_method": submission.payment_method_id,
                "paid_via_employee": submission.paid_via_employee,
                "notes": submission.notes,
            },
        )

    return render(
        request,
        "sales/employee_payment_edit.html",
        {
            "title": _("Edit payment"),
            "pay_sub_form": form,
            "submission": submission,
            "payment_methods": payment_methods,
            "acting_employee": acting_employee,
        },
    )


@employee_required
@require_POST
def employee_payment_delete(request, pk):
    """Undo/delete one of the employee's own payment submissions."""
    submission = get_object_or_404(
        CustomerPaymentSubmission, pk=pk, created_by=request.user
    )
    htmx = is_htmx(request)
    if not payment_submission_is_employee_deletable(submission, request.user):
        msg = _("You can only edit or delete your last %(count)s payments.") % {
            "count": EMPLOYEE_RECENT_EDIT_LIMIT
        }
        if htmx:
            return htmx_action_error(str(msg))
        messages.error(request, msg)
        return redirect("sales:employee_recent_sales")
    try:
        delete_employee_customer_payment_submission(
            submission=submission, user=request.user
        )
    except ValueError as exc:
        if htmx:
            return htmx_action_error(str(exc))
        messages.error(request, str(exc))
        return redirect("sales:employee_recent_sales")
    except (IntegrityError, DatabaseError) as exc:
        msg = _("Could not delete this payment: %(reason)s") % {"reason": str(exc)}
        if htmx:
            return htmx_action_error(str(msg))
        messages.error(request, msg)
        return redirect("sales:employee_recent_sales")
    if htmx:
        return htmx_remove_target()
    messages.success(request, _("Payment entry was removed."))
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
