from decimal import Decimal

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from accounts.permissions import employee_required, is_management, management_required
from companies.models import ProductLine
from core.pagination import paginate_request
from customers.models import Customer
from inventory.forms import (
    AdjustBalanceForm,
    AllocateToCustomerForm,
    ClearBalanceForm,
    CustomerStockFilterForm,
    MarkDamagedForm,
    MovementFilterForm,
    ReceiveMainStockForm,
    ReturnFromCustomerForm,
    SetBalanceQuantityForm,
    SimCardSearchForm,
)
from inventory.line_utils import canonical_product_line, distinct_sim_product_lines
from inventory.models import SimCard, SimStockBalance, SimStockMovement
from inventory.serial_utils import parse_serial_list
from inventory.services import (
    adjust_balance,
    allocate_to_customer,
    clear_balance,
    delete_balance_row,
    delete_movement,
    ensure_main_balance,
    mark_damaged,
    receive_main_stock,
    return_from_customer,
    set_balance_quantity,
)


def _overview_rows():
    rows = []
    show_valuation = False
    for line in distinct_sim_product_lines():
        canonical = canonical_product_line(line)
        main_balance = SimStockBalance.objects.filter(
            location=SimStockBalance.Location.MAIN,
            product_line=canonical,
        ).first()
        main_qty = main_balance.quantity if main_balance else 0
        cust_qty = (
            SimStockBalance.objects.filter(
                location=SimStockBalance.Location.CUSTOMER,
                product_line=canonical,
                quantity__gt=0,
            ).aggregate(t=Sum("quantity"))["t"]
            or 0
        )
        unit_cost = canonical.estimated_unit_cost
        if unit_cost is not None and unit_cost > 0:
            show_valuation = True
        total_qty = main_qty + cust_qty
        rows.append(
            {
                "line": canonical,
                "main_balance": main_balance,
                "main_qty": main_qty,
                "customer_qty": cust_qty,
                "total_qty": total_qty,
                "unit_cost": unit_cost,
                "main_value": (unit_cost or Decimal("0")) * main_qty,
                "customer_value": (unit_cost or Decimal("0")) * cust_qty,
                "total_value": (unit_cost or Decimal("0")) * total_qty,
            }
        )
    return rows, show_valuation


def _inventory_base_template(request):
    return "base_management.html" if is_management(request.user) else "base_employee.html"


def _redirect_next(request, fallback_name: str, **fallback_kwargs):
    target = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if target:
        return redirect(target)
    return redirect(fallback_name, **fallback_kwargs)


@employee_required
def inventory_overview(request):
    rows, show_valuation = _overview_rows()
    return render(
        request,
        "inventory/overview.html",
        {
            "title": _("SIM inventory"),
            "rows": rows,
            "show_valuation": show_valuation,
            "read_only": not is_management(request.user),
            "base_template": _inventory_base_template(request),
        },
    )


@employee_required
def inventory_main(request):
    line_ids = [line.pk for line in distinct_sim_product_lines()]
    balances = (
        SimStockBalance.objects.filter(
            location=SimStockBalance.Location.MAIN,
            product_line_id__in=line_ids,
        )
        .select_related("product_line")
        .order_by("product_line__name")
    )
    receive_form = None
    read_only = not is_management(request.user)
    if not read_only and request.method == "POST" and request.POST.get("action") == "receive":
        receive_form = ReceiveMainStockForm(request.POST)
        if receive_form.is_valid():
            try:
                receive_main_stock(
                    product_line=receive_form.cleaned_data["product_line"],
                    qty=receive_form.cleaned_data["quantity"],
                    notes=receive_form.cleaned_data.get("notes") or "",
                    user=request.user,
                    serials=parse_serial_list(receive_form.cleaned_data.get("serials") or ""),
                )
                messages.success(request, _("Main stock updated."))
                return redirect("inventory:main")
            except ValueError as exc:
                messages.error(request, str(exc))
    if receive_form is None and not read_only:
        receive_form = ReceiveMainStockForm()
    return render(
        request,
        "inventory/main_stock.html",
        {
            "title": _("Main SIM stock"),
            "balances": balances,
            "receive_form": receive_form,
            "read_only": read_only,
            "base_template": _inventory_base_template(request),
        },
    )


@management_required
@require_POST
def inventory_overview_init_main(request, line_id):
    line = get_object_or_404(ProductLine, pk=line_id, is_active=True)
    form = SetBalanceQuantityForm(request.POST)
    if form.is_valid():
        try:
            balance = ensure_main_balance(line)
            set_balance_quantity(
                balance=balance,
                new_quantity=form.cleaned_data["quantity"],
                reason=form.cleaned_data["reason"],
                user=request.user,
            )
            messages.success(request, _("Main stock set."))
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, _("Invalid form."))
    return _redirect_next(request, "inventory:overview")


@management_required
@require_POST
def inventory_main_set(request, pk):
    balance = get_object_or_404(
        SimStockBalance,
        pk=pk,
        location=SimStockBalance.Location.MAIN,
    )
    form = SetBalanceQuantityForm(request.POST)
    if form.is_valid():
        try:
            set_balance_quantity(
                balance=balance,
                new_quantity=form.cleaned_data["quantity"],
                reason=form.cleaned_data["reason"],
                user=request.user,
            )
            messages.success(request, _("Quantity updated."))
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, _("Invalid form."))
    return _redirect_next(request, "inventory:main")


@management_required
@require_POST
def inventory_main_adjust(request, pk):
    balance = get_object_or_404(
        SimStockBalance,
        pk=pk,
        location=SimStockBalance.Location.MAIN,
    )
    form = AdjustBalanceForm(request.POST)
    if form.is_valid():
        try:
            adjust_balance(
                balance=balance,
                signed_delta=form.cleaned_data["signed_delta"],
                reason=form.cleaned_data["reason"],
                user=request.user,
            )
            messages.success(request, _("Balance adjusted."))
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, _("Invalid adjustment."))
    return _redirect_next(request, "inventory:main")


@management_required
@require_POST
def inventory_main_damaged(request, pk):
    balance = get_object_or_404(
        SimStockBalance,
        pk=pk,
        location=SimStockBalance.Location.MAIN,
    )
    form = MarkDamagedForm(request.POST)
    if form.is_valid():
        try:
            mark_damaged(
                balance=balance,
                qty=form.cleaned_data["quantity"],
                notes=form.cleaned_data.get("notes") or "",
                user=request.user,
            )
            messages.success(request, _("Damaged stock recorded."))
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, _("Invalid form."))
    return _redirect_next(request, "inventory:main")


@management_required
@require_POST
def inventory_main_clear(request, pk):
    balance = get_object_or_404(
        SimStockBalance,
        pk=pk,
        location=SimStockBalance.Location.MAIN,
    )
    form = ClearBalanceForm(request.POST)
    reason = form.cleaned_data.get("reason") if form.is_valid() else ""
    try:
        clear_balance(balance=balance, reason=reason or str(_("Cleared for testing")), user=request.user)
        messages.success(request, _("Balance cleared."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return _redirect_next(request, "inventory:main")


@management_required
@require_POST
def inventory_main_delete(request, pk):
    balance = get_object_or_404(
        SimStockBalance,
        pk=pk,
        location=SimStockBalance.Location.MAIN,
    )
    try:
        delete_balance_row(balance=balance, user=request.user)
        messages.success(request, _("Balance row deleted."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return _redirect_next(request, "inventory:main")


@employee_required
def inventory_customers(request):
    qs = (
        SimStockBalance.objects.filter(
            location=SimStockBalance.Location.CUSTOMER,
            quantity__gt=0,
        )
        .select_related("customer", "product_line")
        .order_by("customer__name", "product_line__name")
    )
    form = CustomerStockFilterForm(request.GET or None)
    if form.is_valid():
        d = form.cleaned_data
        if d.get("q"):
            qs = qs.filter(customer__name__icontains=d["q"])
        if d.get("product_line"):
            qs = qs.filter(product_line=canonical_product_line(d["product_line"]))
    page_obj = paginate_request(request, qs)

    read_only = not is_management(request.user)
    allocate_form = None
    if not read_only and request.method == "POST" and request.POST.get("action") == "allocate":
        allocate_form = AllocateToCustomerForm(request.POST, hide_customer=True)
        if allocate_form.is_valid():
            try:
                allocate_to_customer(
                    customer=allocate_form.cleaned_data["customer"],
                    product_line=allocate_form.cleaned_data["product_line"],
                    qty=allocate_form.cleaned_data["quantity"],
                    notes=allocate_form.cleaned_data.get("notes") or "",
                    user=request.user,
                    serials=parse_serial_list(allocate_form.cleaned_data.get("serials") or ""),
                )
                messages.success(request, _("Stock allocated to customer."))
                target = request.get_full_path().split("?")[0]
                qd = request.GET.urlencode()
                return redirect(f"{target}?{qd}" if qd else target)
            except ValueError as exc:
                messages.error(request, str(exc))
    if allocate_form is None and not read_only:
        allocate_form = AllocateToCustomerForm(hide_customer=True)

    filter_active = 0
    if form.is_valid():
        if form.cleaned_data.get("q"):
            filter_active += 1
        if form.cleaned_data.get("product_line"):
            filter_active += 1

    allocate_panel_open = (
        not read_only
        and request.method == "POST"
        and request.POST.get("action") == "allocate"
    )

    return render(
        request,
        "inventory/customer_list.html",
        {
            "title": _("Customer SIM stock"),
            "page_obj": page_obj,
            "filter_form": form,
            "allocate_form": allocate_form,
            "filter_active": filter_active,
            "allocate_panel_open": allocate_panel_open,
            "read_only": read_only,
            "base_template": _inventory_base_template(request),
        },
    )


@employee_required
def inventory_customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    balances = (
        SimStockBalance.objects.filter(
            location=SimStockBalance.Location.CUSTOMER,
            customer=customer,
        )
        .select_related("product_line")
        .order_by("product_line__name")
    )
    read_only = not is_management(request.user)
    return_form = None
    if not read_only and request.method == "POST" and request.POST.get("action") == "return":
        return_form = ReturnFromCustomerForm(request.POST)
        if return_form.is_valid():
            try:
                return_from_customer(
                    customer=customer,
                    product_line=return_form.cleaned_data["product_line"],
                    qty=return_form.cleaned_data["quantity"],
                    notes=return_form.cleaned_data.get("notes") or "",
                    user=request.user,
                    serials=parse_serial_list(return_form.cleaned_data.get("serials") or ""),
                )
                messages.success(request, _("Stock returned to main."))
                return redirect("inventory:customer_detail", pk=customer.pk)
            except ValueError as exc:
                messages.error(request, str(exc))
    if return_form is None and not read_only:
        return_form = ReturnFromCustomerForm()

    return_panel_open = (
        not read_only
        and request.method == "POST"
        and request.POST.get("action") == "return"
    )

    return render(
        request,
        "inventory/customer_detail.html",
        {
            "title": customer.name,
            "customer": customer,
            "balances": balances,
            "return_form": return_form,
            "return_panel_open": return_panel_open,
            "read_only": read_only,
            "base_template": _inventory_base_template(request),
        },
    )


@management_required
@require_POST
def inventory_customer_set(request, pk, balance_pk):
    customer = get_object_or_404(Customer, pk=pk)
    balance = get_object_or_404(
        SimStockBalance,
        pk=balance_pk,
        location=SimStockBalance.Location.CUSTOMER,
        customer=customer,
    )
    form = SetBalanceQuantityForm(request.POST)
    if form.is_valid():
        try:
            set_balance_quantity(
                balance=balance,
                new_quantity=form.cleaned_data["quantity"],
                reason=form.cleaned_data["reason"],
                user=request.user,
            )
            messages.success(request, _("Quantity updated."))
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, _("Invalid form."))
    return _redirect_next(request, "inventory:customer_detail", pk=customer.pk)


@management_required
@require_POST
def inventory_customer_adjust(request, pk, balance_pk):
    customer = get_object_or_404(Customer, pk=pk)
    balance = get_object_or_404(
        SimStockBalance,
        pk=balance_pk,
        location=SimStockBalance.Location.CUSTOMER,
        customer=customer,
    )
    form = AdjustBalanceForm(request.POST)
    if form.is_valid():
        try:
            adjust_balance(
                balance=balance,
                signed_delta=form.cleaned_data["signed_delta"],
                reason=form.cleaned_data["reason"],
                user=request.user,
            )
            messages.success(request, _("Balance adjusted."))
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, _("Invalid adjustment."))
    return _redirect_next(request, "inventory:customer_detail", pk=customer.pk)


@management_required
@require_POST
def inventory_customer_damaged(request, pk, balance_pk):
    customer = get_object_or_404(Customer, pk=pk)
    balance = get_object_or_404(
        SimStockBalance,
        pk=balance_pk,
        location=SimStockBalance.Location.CUSTOMER,
        customer=customer,
    )
    form = MarkDamagedForm(request.POST)
    if form.is_valid():
        try:
            mark_damaged(
                balance=balance,
                qty=form.cleaned_data["quantity"],
                notes=form.cleaned_data.get("notes") or "",
                user=request.user,
            )
            messages.success(request, _("Damaged stock recorded."))
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, _("Invalid form."))
    return _redirect_next(request, "inventory:customer_detail", pk=customer.pk)


@management_required
@require_POST
def inventory_customer_clear(request, pk, balance_pk):
    customer = get_object_or_404(Customer, pk=pk)
    balance = get_object_or_404(
        SimStockBalance,
        pk=balance_pk,
        location=SimStockBalance.Location.CUSTOMER,
        customer=customer,
    )
    form = ClearBalanceForm(request.POST)
    reason = form.cleaned_data.get("reason") if form.is_valid() else ""
    try:
        clear_balance(balance=balance, reason=reason or str(_("Cleared for testing")), user=request.user)
        messages.success(request, _("Balance cleared."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return _redirect_next(request, "inventory:customer_detail", pk=customer.pk)


@management_required
@require_POST
def inventory_customer_delete(request, pk, balance_pk):
    customer = get_object_or_404(Customer, pk=pk)
    balance = get_object_or_404(
        SimStockBalance,
        pk=balance_pk,
        location=SimStockBalance.Location.CUSTOMER,
        customer=customer,
    )
    try:
        delete_balance_row(balance=balance, user=request.user)
        messages.success(request, _("Balance row deleted."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return _redirect_next(request, "inventory:customer_detail", pk=customer.pk)


@employee_required
def inventory_movements(request):
    qs = SimStockMovement.objects.select_related(
        "product_line",
        "customer",
        "sale",
        "created_by",
        "from_balance",
        "to_balance",
    )
    form = MovementFilterForm(request.GET or None)
    if form.is_valid():
        d = form.cleaned_data
        if d.get("movement_type"):
            qs = qs.filter(movement_type=d["movement_type"])
        if d.get("product_line"):
            qs = qs.filter(product_line=canonical_product_line(d["product_line"]))
        if d.get("customer"):
            qs = qs.filter(customer=d["customer"])
        if d.get("date_from"):
            qs = qs.filter(created_at__date__gte=d["date_from"])
        if d.get("date_to"):
            qs = qs.filter(created_at__date__lte=d["date_to"])
    page_obj = paginate_request(request, qs)
    return render(
        request,
        "inventory/movements.html",
        {
            "title": _("SIM stock movements"),
            "page_obj": page_obj,
            "filter_form": form,
            "read_only": not is_management(request.user),
            "base_template": _inventory_base_template(request),
        },
    )


@employee_required
def inventory_line_detail(request, line_id):
    line = get_object_or_404(ProductLine, pk=line_id, is_active=True)
    canonical = canonical_product_line(line)

    main_balance = SimStockBalance.objects.filter(
        location=SimStockBalance.Location.MAIN,
        product_line=canonical,
    ).first()
    main_qty = main_balance.quantity if main_balance else 0

    customer_balances = (
        SimStockBalance.objects.filter(
            location=SimStockBalance.Location.CUSTOMER,
            product_line=canonical,
            quantity__gt=0,
        )
        .select_related("customer")
        .order_by("customer__name")
    )
    customer_qty = sum(b.quantity for b in customer_balances)

    movements = (
        SimStockMovement.objects.filter(product_line=canonical)
        .select_related("customer", "sale", "created_by")
        .order_by("-created_at")[:80]
    )

    sim_cards = (
        SimCard.objects.filter(product_line=canonical)
        .select_related("customer", "sale")
        .order_by("status", "serial_or_iccid", "-created_at")
    )

    read_only = not is_management(request.user)
    movements_url_name = "inventory:employee_movements" if read_only else "inventory:movements"
    cards_url_name = "inventory:employee_cards" if read_only else "inventory:cards"

    return render(
        request,
        "inventory/line_detail.html",
        {
            "title": line.name,
            "line": canonical,
            "main_balance": main_balance,
            "main_qty": main_qty,
            "customer_balances": customer_balances,
            "customer_qty": customer_qty,
            "total_qty": main_qty + customer_qty,
            "movements": movements,
            "sim_cards": sim_cards,
            "movements_url": movements_url_name,
            "cards_url": cards_url_name,
            "read_only": read_only,
            "base_template": _inventory_base_template(request),
        },
    )


@employee_required
def inventory_cards(request):
    qs = SimCard.objects.select_related("product_line", "customer", "sale").order_by("-created_at")
    form = SimCardSearchForm(request.GET or None)
    if form.is_valid() and form.cleaned_data.get("q"):
        q = form.cleaned_data["q"]
        qs = qs.filter(serial_or_iccid__icontains=q)
    page_obj = paginate_request(request, qs)
    return render(
        request,
        "inventory/cards.html",
        {
            "title": _("SIM cards"),
            "page_obj": page_obj,
            "filter_form": form,
            "read_only": not is_management(request.user),
            "base_template": _inventory_base_template(request),
        },
    )


@management_required
@require_POST
def inventory_movement_delete(request, pk):
    movement = get_object_or_404(SimStockMovement, pk=pk)
    try:
        delete_movement(movement=movement, user=request.user)
        messages.success(request, _("Movement deleted."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return _redirect_next(request, "inventory:movements")
