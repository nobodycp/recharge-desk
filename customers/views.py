from decimal import Decimal

from django.contrib import messages
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import employee_required, management_required
from core.pagination import paginate_request
from customers.forms import CustomerForm, CustomerPaymentForm, CustomerPhoneForm
from customers.models import Customer, CustomerLedger, CustomerPayment, CustomerPhone
from customers.services import (
    add_customer_phone,
    create_customer,
    record_customer_payment,
)


@management_required
def customer_list(request):
    qs = Customer.objects.all()
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phones__phone__icontains=q)).distinct()
    debt_total = (
        Customer.objects.filter(current_balance__gt=0)
        .aggregate(s=Sum("current_balance"))["s"]
        or Decimal("0")
    )
    credit_total = (
        Customer.objects.filter(current_balance__lt=0)
        .aggregate(s=Sum("current_balance"))["s"]
        or Decimal("0")
    )
    qs = qs.order_by("-current_balance", "name")
    page_obj = paginate_request(request, qs)
    return render(
        request,
        "customers/customer_list.html",
        {
            "page_obj": page_obj,
            "title": _("Customers"),
            "q": q,
            "debt_total": debt_total,
            "credit_total": credit_total,
        },
    )


@management_required
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            customer = create_customer(
                name=form.cleaned_data["name"],
                phones=[form.cleaned_data.get("initial_phone") or ""],
                notes=form.cleaned_data.get("notes") or "",
                user=request.user,
            )
            customer.is_active = form.cleaned_data["is_active"]
            customer.save(update_fields=["is_active"])
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Customer created."))
            return redirect("customers:customer_detail", pk=customer.pk)
    return render(
        request,
        "customers/customer_form.html",
        {"form": form, "title": _("New customer")},
    )


@management_required
def customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Customer updated."))
        return redirect("customers:customer_detail", pk=customer.pk)
    return render(
        request,
        "customers/customer_form.html",
        {"form": form, "title": _("Edit customer"), "customer": customer},
    )


@management_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    phones = list(customer.phones.order_by("phone"))

    from sales.models import Sale  # local import to avoid cycle

    on_account_sales = (
        Sale.objects.filter(customer=customer, on_account=True)
        .select_related("company", "product", "product__line", "payment_method", "created_by")
        .order_by("-created_at")[:200]
    )
    payments = (
        customer.payments.select_related("payment_method", "created_by").order_by("-created_at")[:200]
    )
    ledger = (
        customer.ledger_entries.select_related("sale", "payment", "created_by").order_by("-created_at")[:200]
    )

    awaiting_count = on_account_sales.filter(status=Sale.Status.AWAITING).count()
    pending_count = on_account_sales.filter(status=Sale.Status.PENDING).count()

    payment_form = CustomerPaymentForm()
    phone_form = CustomerPhoneForm()

    return render(
        request,
        "customers/customer_detail.html",
        {
            "title": customer.name,
            "customer": customer,
            "phones": phones,
            "on_account_sales": on_account_sales,
            "payments": payments,
            "ledger": ledger,
            "awaiting_count": awaiting_count,
            "pending_count": pending_count,
            "payment_form": payment_form,
            "phone_form": phone_form,
        },
    )


@management_required
@require_POST
def customer_record_payment(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerPaymentForm(request.POST)
    if not form.is_valid():
        for field, errs in form.errors.items():
            for e in errs:
                messages.error(request, f"{field}: {e}")
        return redirect("customers:customer_detail", pk=customer.pk)
    try:
        record_customer_payment(
            customer=customer,
            amount=form.cleaned_data["amount"],
            payment_method=form.cleaned_data["payment_method"],
            notes=form.cleaned_data.get("notes") or "",
            user=request.user,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Payment recorded."))
    return redirect("customers:customer_detail", pk=customer.pk)


@management_required
@require_POST
def customer_add_phone(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerPhoneForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Phone is required."))
        return redirect("customers:customer_detail", pk=customer.pk)
    try:
        add_customer_phone(
            customer=customer,
            phone=form.cleaned_data["phone"],
            label=form.cleaned_data.get("label") or "",
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Phone added."))
    return redirect("customers:customer_detail", pk=customer.pk)


@management_required
@require_POST
def customer_remove_phone(request, pk, phone_id):
    customer = get_object_or_404(Customer, pk=pk)
    phone = get_object_or_404(CustomerPhone, pk=phone_id, customer=customer)
    phone.delete()
    messages.success(request, _("Phone removed."))
    return redirect("customers:customer_detail", pk=customer.pk)


@employee_required
@require_GET
def api_customer_lookup(request):
    """Typeahead for the entry form: match name or phone, return id+name+balance."""
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    qs = (
        Customer.objects.filter(is_active=True)
        .filter(Q(name__icontains=q) | Q(phones__phone__icontains=q))
        .distinct()
        .order_by("name")[:10]
    )
    results = [
        {
            "id": c.pk,
            "name": c.name,
            "balance": str(c.current_balance),
        }
        for c in qs
    ]
    return JsonResponse({"results": results})


@employee_required
@require_POST
def api_customer_create(request):
    """Inline create from the entry form. Returns JSON id+name."""
    name = (request.POST.get("name") or "").strip()
    phone = (request.POST.get("phone") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
    try:
        customer = create_customer(
            name=name,
            phones=[phone] if phone else None,
            user=request.user,
        )
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "id": customer.pk, "name": customer.name})
