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
from core.http_utils import get_client_ip
from core.models import AppSettings
from core.sale_workflow import finalize_payment_submission_after_entry, finalize_sale_after_entry
from customers.forms import EmployeeCustomerPaymentSubmissionForm
from customers.services import resolve_or_create_customer_for_sale, submit_customer_payment_submission
from customers.views._shared import flash_form_errors
from phone_refresh.models import RefreshSource, SystemSettings
from phone_refresh.services.refresh_service import refresh_phone
from phone_refresh.validation import is_valid_phone
from sales.forms import EmployeeRecentFilterForm, EmployeeSaleEditForm, EmployeeSaleForm
from sales.models import PaymentMethod, Sale
from sales.employee_editing import (
    EMPLOYEE_RECENT_EDIT_LIMIT,
    employee_editable_sale_ids,
    sale_is_employee_editable,
)
from sales.payer_lookup import latest_sale_for_reference, payer_name_suggestions
from sales.pricing import ESIM_EXTRA_COST
from inventory.services import preview_sim_stock_deduction
from employees.services import get_acting_employee_profile
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


def _phone_registered_for_refresh(phone: str) -> bool:
    """True when a non-cancelled sale exists with this reference number."""
    ref = (phone or "").strip()
    if not ref:
        return False
    return (
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .filter(reference_number=ref)
        .exists()
    )


def _refresh_json_payload(result) -> dict:
    payload = {
        "status": result.status.code,
        "message": {"title": result.message_title, "body": result.message_body},
    }
    if SystemSettings.get().show_last_refresh:
        payload["last_refresh_at"] = (
            result.last_refresh_at.isoformat()
            if result.last_refresh_at is not None
            else None
        )
        payload["seconds_since_last_refresh"] = result.seconds_since_last_refresh
    return payload


@employee_required
def employee_entry(request):
    company_id = request.POST.get("company") or request.GET.get("company")
    initial = {}
    if request.method == "GET" and company_id:
        initial["company"] = company_id
    form = EmployeeSaleForm(
        request.POST or None,
        company_id=company_id,
        initial=initial,
        user=request.user,
    )
    companies = list(Company.objects.filter(is_active=True).order_by("name"))
    payment_methods = list(PaymentMethod.objects.filter(is_active=True).order_by("name"))
    acting_employee = get_acting_employee_profile(request.user)
    product_groups = _build_product_groups(company_id) if company_id else []

    recent_qs = Sale.objects.select_related(
        "company",
        "product",
        "product__line",
        "payment_method",
        "employee_recipient",
        "employee_recipient__user",
        "employee_recipient__user__profile",
    )
    if is_employee(request.user):
        recent_qs = recent_qs.filter(created_by=request.user)
    editable_sale_ids = employee_editable_sale_ids(request.user)
    recent = recent_qs.order_by("-created_at")[:EMPLOYEE_RECENT_EDIT_LIMIT]

    if request.method == "POST" and form.is_valid():
        try:
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
            sale = create_sale(
                company=form.cleaned_data["company"],
                product=form.cleaned_data["product"],
                reference_number=form.cleaned_data["reference_number"],
                payer_name=form.cleaned_data["payer_name"],
                payment_method=form.cleaned_data["payment_method"]
                if not on_account and not paid_via_employee
                else None,
                sell_price_actual=form.cleaned_data["sell_price_actual"],
                notes=form.cleaned_data.get("notes") or "",
                user=request.user,
                is_esim=bool(form.cleaned_data.get("is_esim")),
                is_new_sim=is_new_sim,
                sim_serial_or_iccid=form.cleaned_data.get("sim_serial_or_iccid") or "",
                on_account=on_account,
                customer=customer,
                paid_via_employee=paid_via_employee,
                employee_recipient=employee_recipient,
            )
            outcome = finalize_sale_after_entry(
                sale=sale,
                user=request.user,
                on_account=on_account,
                paid_via_employee=paid_via_employee,
            )
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
            "editable_sale_ids": editable_sale_ids,
            "product_groups": product_groups,
            "companies": companies,
            "payment_methods": payment_methods,
            "acting_employee": acting_employee,
            "selected_company_id": str(company_id or ""),
            "selected_line_id": selected_line_id,
            "selected_product_id": selected_product_id,
            "selected_payment_id": str(request.POST.get("payment_method", "") or ""),
            "esim_extra": ESIM_EXTRA_COST,
            "pay_sub_form": EmployeeCustomerPaymentSubmissionForm(),
        },
    )


@employee_required
@require_POST
def employee_submit_customer_payment_submission(request):
    if not AppSettings.load().sales_show_record_payment:
        messages.error(request, _("Recording payments from the sales screen is disabled."))
        return redirect("sales:employee_entry")
    form = EmployeeCustomerPaymentSubmissionForm(request.POST)
    if not form.is_valid():
        flash_form_errors(request, form)
        return redirect("sales:employee_entry")
    try:
        submission = submit_customer_payment_submission(
            customer=form.cleaned_data["customer"],
            amount=form.cleaned_data["amount"],
            payment_method=form.cleaned_data["payment_method"],
            notes=form.cleaned_data.get("notes") or "",
            user=request.user,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        if finalize_payment_submission_after_entry(submission=submission, user=request.user):
            messages.success(request, _("Payment recorded on the customer account."))
        else:
            messages.success(request, _("Payment submitted for management approval."))
    return redirect("sales:employee_entry")


@employee_required
@require_POST
def employee_refresh_phone(request):
    """Refresh a registered phone for staff — no public API rate limits."""
    if not AppSettings.load().sales_show_refresh_phone:
        return JsonResponse(
            {
                "status": "error",
                "message": {
                    "title": str(_("Error")),
                    "body": str(_("Phone refresh is disabled on the sales screen.")),
                },
            },
            status=403,
        )
    phone = (request.POST.get("phone") or "").strip()
    if not phone:
        return JsonResponse(
            {
                "status": "error",
                "message": {
                    "title": str(_("Error")),
                    "body": str(_("Phone number is required.")),
                },
            },
            status=400,
        )
    if not is_valid_phone(phone):
        return JsonResponse(
            {
                "status": "error",
                "message": {
                    "title": str(_("Error")),
                    "body": str(
                        _(
                            "Phone must be 10 digits and start with "
                            "050, 051, 052, 053, 054, 055, or 058."
                        )
                    ),
                },
            },
            status=400,
        )
    if not _phone_registered_for_refresh(phone):
        return JsonResponse(
            {
                "status": "not_found",
                "message": {
                    "title": str(_("Not registered")),
                    "body": str(
                        _("This phone number is not registered in the system.")
                    ),
                },
            },
            status=400,
        )
    result = refresh_phone(
        phone,
        ip=get_client_ip(request) or None,
        source=RefreshSource.EMPLOYEE,
    )
    return JsonResponse(_refresh_json_payload(result))


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
    return JsonResponse({"suggestions": [{"name": x["name"], "count": x["count"]} for x in items]})


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
    if data.get("status"):
        qs = qs.filter(status=data["status"])

    date_from = data.get("date_from")
    date_to = data.get("date_to")
    if not date_from and not date_to and default_date is not None:
        date_from = date_to = default_date
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    return qs.order_by("-created_at"), date_from, date_to


@employee_required
def employee_recent_sales(request):
    """Listing of the current employee's sales with edit/delete buttons
    for entries management has not yet acted on.

    Defaults to today; an optional collapsible filter card lets the
    employee narrow by free-text (number/name), company, payment
    method, status, and date range — any subset, in any combination."""
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
    sales = list(sales_qs)
    editable_sale_ids = employee_editable_sale_ids(request.user)

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
            "sales": sales,
            "editable_sale_ids": editable_sale_ids,
            "filter_form": filter_form,
            "scope_label": scope_label,
            "is_default_today": is_default_today,
        },
    )


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
        messages.error(
            request,
            _("You can only edit or delete your last %(count)s entries.")
            % {"count": EMPLOYEE_RECENT_EDIT_LIMIT},
        )
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
        msg = _("You can only edit or delete your last %(count)s entries.") % {
            "count": EMPLOYEE_RECENT_EDIT_LIMIT
        }
        if htmx:
            return htmx_action_error(str(msg))
        messages.error(request, msg)
        return redirect("sales:employee_recent_sales")
    try:
        delete_sale_permanently(sale=sale, user=request.user)
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
