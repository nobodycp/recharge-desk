"""Domain services for the customers / accounts-receivable subsystem.

All write paths go through these helpers so the Sale ledger, the customer
ledger, and the customer balance stay consistent.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from customers.models import Customer, CustomerLedger, CustomerPayment, CustomerPhone


def _apply_balance_delta(customer: Customer, delta: Decimal) -> None:
    Customer.objects.filter(pk=customer.pk).update(
        current_balance=F("current_balance") + delta
    )
    customer.refresh_from_db(fields=["current_balance"])


def get_or_create_customer_for_phone(phone: str) -> Optional[Customer]:
    """Return the most recent customer associated with ``phone``.

    A phone can legitimately belong to several customers over time (re-issued
    SIMs, family-shared lines), so the lookup is non-authoritative — used only
    for hints in the UI. The actual identity tied to a sale is the payer name.
    """
    phone = (phone or "").strip()
    if not phone:
        return None
    link = (
        CustomerPhone.objects.select_related("customer")
        .filter(phone__iexact=phone)
        .order_by("-created_at", "-id")
        .first()
    )
    return link.customer if link else None


@transaction.atomic
def create_customer(*, name: str, phones: Optional[List[str]] = None, notes: str = "", user) -> Customer:
    name = (name or "").strip()
    if not name:
        raise ValueError("Customer name is required.")
    customer = Customer.objects.create(
        name=name,
        notes=(notes or "").strip(),
        created_by=user,
    )
    for raw in phones or []:
        phone = (raw or "").strip()
        if not phone:
            continue
        CustomerPhone.objects.get_or_create(phone=phone, defaults={"customer": customer})
    return customer


@transaction.atomic
def resolve_or_create_customer_for_sale(*, name: str, phone: str = "", user) -> Customer:
    """Auto-resolve the customer for an on-account employee sale.

    Identity is the payer **name** — one person can own many SIMs, the same
    phone can pass through different people over time. Lookup is therefore:

    1. Match an active customer by exact (case-insensitive) name.
    2. If found, attach the phone to *that* customer if it isn't already
       on their record (other customers may legitimately share the number).
    3. Otherwise create a new Customer with that name and link the phone.
    """
    name = (name or "").strip()
    phone = (phone or "").strip()
    if not name:
        raise ValueError("Payer name is required for on-account sales.")

    customer = Customer.objects.filter(is_active=True, name__iexact=name).first()
    if customer is None:
        customer = Customer.objects.create(name=name, created_by=user)

    if phone and not CustomerPhone.objects.filter(customer=customer, phone__iexact=phone).exists():
        CustomerPhone.objects.create(customer=customer, phone=phone)
    return customer


@transaction.atomic
def add_customer_phone(*, customer: Customer, phone: str, label: str = "") -> CustomerPhone:
    phone = (phone or "").strip()
    if not phone:
        raise ValueError("Phone is required.")
    existing = CustomerPhone.objects.filter(customer=customer, phone__iexact=phone).first()
    if existing:
        return existing
    return CustomerPhone.objects.create(customer=customer, phone=phone, label=(label or "").strip())


@transaction.atomic
def approve_sale(*, sale, user):
    """Approve an AWAITING on-account sale: post the customer charge."""
    from sales.models import Sale  # local import to avoid app-loading cycles

    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale_locked.status != Sale.Status.AWAITING:
        raise ValueError("Only awaiting sales can be approved.")
    if not sale_locked.on_account or sale_locked.customer_id is None:
        raise ValueError("This sale is not flagged as on-account or has no customer.")

    customer_locked = Customer.objects.select_for_update().get(pk=sale_locked.customer_id)

    sale_locked.status = Sale.Status.PENDING
    sale_locked.approved_at = timezone.now()
    sale_locked.approved_by = user
    sale_locked.save(update_fields=["status", "approved_at", "approved_by", "updated_at"])

    CustomerLedger.objects.create(
        customer=customer_locked,
        entry_type=CustomerLedger.EntryType.CHARGE,
        amount=sale_locked.sell_price_actual,
        sale=sale_locked,
        created_by=user,
    )
    _apply_balance_delta(customer_locked, sale_locked.sell_price_actual)

    # New charge may be auto-covered by an existing credit.
    reapply_settlements_for_customer(customer=customer_locked, triggering_payment=None, user=user)
    return sale_locked


@transaction.atomic
def reject_sale(*, sale, user):
    """Reject an AWAITING on-account sale: cancel it (refund supplier balance)."""
    from sales.models import Sale  # local import to avoid app-loading cycles
    from sales.services import cancel_sale

    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale_locked.status != Sale.Status.AWAITING:
        raise ValueError("Only awaiting sales can be rejected.")
    return cancel_sale(sale=sale_locked, user=user)


@transaction.atomic
def record_customer_payment(
    *,
    customer: Customer,
    amount: Decimal,
    payment_method,
    notes: str = "",
    user,
) -> CustomerPayment:
    """Record a real-money payment from a customer and run FIFO settlement."""
    if amount is None or Decimal(amount) <= 0:
        raise ValueError("Payment amount must be positive.")

    customer_locked = Customer.objects.select_for_update().get(pk=customer.pk)
    payment = CustomerPayment.objects.create(
        customer=customer_locked,
        amount=amount,
        payment_method=payment_method,
        notes=(notes or "").strip(),
        created_by=user,
    )
    CustomerLedger.objects.create(
        customer=customer_locked,
        entry_type=CustomerLedger.EntryType.PAYMENT,
        amount=amount,
        payment=payment,
        created_by=user,
    )
    _apply_balance_delta(customer_locked, -Decimal(amount))

    reapply_settlements_for_customer(
        customer=customer_locked, triggering_payment=payment, user=user
    )
    return payment


def _pick_settling_payment(customer: Customer):
    """Pick a CustomerPayment to stamp on a sale being auto-settled.

    Prefer the most recent payment; fall back to None when the credit
    pre-dates any payment row (shouldn't normally happen).
    """
    return customer.payments.order_by("-created_at", "-id").first()


@transaction.atomic
def reapply_settlements_for_customer(*, customer: Customer, triggering_payment, user) -> int:
    """FIFO-settle the customer's pending on-account charges.

    Sums payments minus the value of charges that have already been settled
    (i.e. on-account sales already flipped to PAID), and walks the remaining
    PENDING on-account sales oldest-first, marking each one PAID once the
    available pot covers it. Returns the number of sales newly settled.
    """
    from sales.models import Sale  # local import to avoid app-loading cycles

    from django.db.models import Sum

    customer_locked = Customer.objects.select_for_update().get(pk=customer.pk)

    payments_total = (
        customer_locked.payments.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    settled_total = (
        Sale.objects.filter(
            customer=customer_locked,
            on_account=True,
            status=Sale.Status.PAID,
        ).aggregate(s=Sum("sell_price_actual"))["s"]
        or Decimal("0")
    )
    available = Decimal(payments_total) - Decimal(settled_total)
    if available <= 0:
        return 0

    fallback_payment = triggering_payment or _pick_settling_payment(customer_locked)
    if fallback_payment is None:
        return 0

    pending = (
        Sale.objects.select_for_update()
        .filter(
            customer=customer_locked,
            on_account=True,
            status=Sale.Status.PENDING,
        )
        .order_by("created_at", "id")
    )

    settled = 0
    now = timezone.now()
    for sale in pending:
        price = Decimal(sale.sell_price_actual)
        if price > available:
            break
        sale.status = Sale.Status.PAID
        sale.paid_at = now
        sale.paid_by = user
        sale.payment_method = fallback_payment.payment_method
        sale.customer_payment = fallback_payment
        sale.save(
            update_fields=[
                "status",
                "paid_at",
                "paid_by",
                "payment_method",
                "customer_payment",
                "updated_at",
            ]
        )
        available -= price
        settled += 1
    return settled
