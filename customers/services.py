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
from django.utils.translation import gettext_lazy as _

from audit.models import AuditAction
from audit.services import record as audit_record
from customers.models import (
    Customer,
    CustomerLedger,
    CustomerPayment,
    CustomerPaymentSubmission,
    CustomerPhone,
)


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
        raise ValueError(_("Customer name is required."))
    customer = Customer.objects.create(
        name=name,
        notes=(notes or "").strip(),
        created_by=user,
    )
    for raw in phones or []:
        phone = (raw or "").strip()
        if not phone:
            continue
        # Unique constraint is (customer, phone) — the same number can legitimately
        # belong to several customers over time (re-issued SIMs, family lines).
        # Lookup must include the customer or get_or_create returns somebody
        # else's row and the new customer ends up with no phone link.
        CustomerPhone.objects.get_or_create(customer=customer, phone=phone)
    audit_record(
        AuditAction.CREATE,
        customer,
        actor=user,
        changes={"name": name, "phones": list(phones or [])},
    )
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
        raise ValueError(_("Payer name is required for on-account sales."))

    customer = Customer.objects.filter(is_active=True, name__iexact=name).first()
    if customer is None:
        from core.models import AppSettings

        if not AppSettings.load().allow_sales_auto_create_customer:
            raise ValueError(
                _(
                    "Customer not found. Check the name or ask management to add the customer."
                )
            )
        customer = Customer.objects.create(name=name, created_by=user)

    if phone and not CustomerPhone.objects.filter(customer=customer, phone__iexact=phone).exists():
        CustomerPhone.objects.create(customer=customer, phone=phone)
    return customer


@transaction.atomic
def add_customer_phone(*, customer: Customer, phone: str, label: str = "") -> CustomerPhone:
    phone = (phone or "").strip()
    if not phone:
        raise ValueError(_("Phone is required."))
    existing = CustomerPhone.objects.filter(customer=customer, phone__iexact=phone).first()
    if existing:
        return existing
    return CustomerPhone.objects.create(customer=customer, phone=phone, label=(label or "").strip())


@transaction.atomic
def post_on_account_sale(*, sale, user):
    """Post an on-account sale to the customer ledger."""
    from sales.models import Sale  # local import to avoid app-loading cycles

    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if not sale_locked.on_account or sale_locked.customer_id is None:
        raise ValueError(_("This sale is not flagged as on-account or has no customer."))

    customer_locked = Customer.objects.select_for_update().get(pk=sale_locked.customer_id)

    if sale_locked.status == Sale.Status.AWAITING:
        sale_locked.status = Sale.Status.PENDING
        sale_locked.approved_at = timezone.now()
        sale_locked.approved_by = user
        sale_locked.save(update_fields=["status", "approved_at", "approved_by", "updated_at"])
    elif sale_locked.status != Sale.Status.PENDING:
        raise ValueError(_("Only awaiting or pending on-account sales can be posted."))

    charge_exists = CustomerLedger.objects.filter(
        customer=customer_locked,
        entry_type=CustomerLedger.EntryType.CHARGE,
        sale=sale_locked,
    ).exists()
    if not charge_exists:
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
    audit_record(
        AuditAction.APPROVE,
        sale_locked,
        actor=user,
        changes={"customer_id": customer_locked.pk, "amount": str(sale_locked.sell_price_actual)},
    )
    from inventory.services import consume_sim_for_sale

    consume_sim_for_sale(sale=sale_locked, user=user)
    return sale_locked


@transaction.atomic
def approve_sale(*, sale, user):
    """Approve an AWAITING on-account sale: post the customer charge."""
    from sales.models import Sale  # local import to avoid app-loading cycles

    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale_locked.status != Sale.Status.AWAITING:
        raise ValueError(_("Only awaiting sales can be approved."))
    return post_on_account_sale(sale=sale_locked, user=user)


@transaction.atomic
def reject_sale(*, sale, user):
    """Reject an AWAITING on-account sale: cancel it (refund supplier balance)."""
    from sales.models import Sale  # local import to avoid app-loading cycles
    from sales.services import cancel_sale

    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale_locked.status != Sale.Status.AWAITING:
        raise ValueError(_("Only awaiting sales can be rejected."))
    result = cancel_sale(sale=sale_locked, user=user)
    audit_record(AuditAction.REJECT, result, actor=user)
    return result


def _apply_customer_payment(
    *,
    customer_locked: Customer,
    amount: Decimal,
    payment_method,
    notes: str,
    user,
) -> CustomerPayment:
    """Persist payment, ledger, balance delta, and FIFO settlement.

    Caller must already hold a row lock on ``customer_locked`` via
    ``select_for_update``.
    """
    amt = Decimal(amount)
    if amt <= 0:
        raise ValueError(_("Payment amount must be positive."))
    payment = CustomerPayment.objects.create(
        customer=customer_locked,
        amount=amt,
        payment_method=payment_method,
        notes=(notes or "").strip(),
        created_by=user,
    )
    CustomerLedger.objects.create(
        customer=customer_locked,
        entry_type=CustomerLedger.EntryType.PAYMENT,
        amount=amt,
        payment=payment,
        created_by=user,
    )
    _apply_balance_delta(customer_locked, -amt)

    reapply_settlements_for_customer(
        customer=customer_locked, triggering_payment=payment, user=user
    )
    audit_record(
        AuditAction.PAY,
        payment,
        actor=user,
        changes={
            "customer_id": customer_locked.pk,
            "amount": str(amt),
            "payment_method_id": getattr(payment_method, "pk", None),
        },
    )
    return payment


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
        raise ValueError(_("Payment amount must be positive."))

    customer_locked = Customer.objects.select_for_update().get(pk=customer.pk)
    return _apply_customer_payment(
        customer_locked=customer_locked,
        amount=Decimal(amount),
        payment_method=payment_method,
        notes=notes or "",
        user=user,
    )


@transaction.atomic
def submit_customer_payment_submission(
    *,
    customer: Customer,
    amount: Decimal,
    payment_method,
    notes: str = "",
    user,
) -> CustomerPaymentSubmission:
    """Create an awaiting payment submission (no ledger/balance change yet)."""
    if not customer.is_active:
        raise ValueError(_("Customer is inactive."))
    amt = Decimal(amount or 0)
    if amt <= 0:
        raise ValueError(_("Payment amount must be positive."))
    sub = CustomerPaymentSubmission.objects.create(
        customer=customer,
        amount=amt,
        payment_method=payment_method,
        notes=(notes or "").strip(),
        created_by=user,
        status=CustomerPaymentSubmission.Status.AWAITING,
    )
    audit_record(
        AuditAction.CREATE,
        sub,
        actor=user,
        changes={
            "customer_id": customer.pk,
            "amount": str(amt),
            "payment_method_id": getattr(payment_method, "pk", None),
        },
    )
    return sub


@transaction.atomic
def approve_customer_payment_submission(
    *, submission: CustomerPaymentSubmission, user
) -> CustomerPayment:
    """Apply the payment and mark the submission approved."""
    sub = CustomerPaymentSubmission.objects.select_for_update().get(pk=submission.pk)
    if sub.status != CustomerPaymentSubmission.Status.AWAITING:
        raise ValueError(_("Only awaiting submissions can be approved."))
    customer_locked = Customer.objects.select_for_update().get(pk=sub.customer_id)
    payment = _apply_customer_payment(
        customer_locked=customer_locked,
        amount=sub.amount,
        payment_method=sub.payment_method,
        notes=sub.notes,
        user=user,
    )
    sub.status = CustomerPaymentSubmission.Status.APPROVED
    sub.approved_by = user
    sub.approved_at = timezone.now()
    sub.save(update_fields=["status", "approved_by", "approved_at"])
    audit_record(
        AuditAction.APPROVE,
        sub,
        actor=user,
        changes={"customer_payment_id": payment.pk},
    )
    from inventory.services import consume_pending_new_sim_for_customer

    consume_pending_new_sim_for_customer(customer=customer_locked, user=user)
    return payment


@transaction.atomic
def reject_customer_payment_submission(
    *, submission: CustomerPaymentSubmission, user, reason: str = ""
) -> None:
    """Decline a submission without touching balances."""
    sub = CustomerPaymentSubmission.objects.select_for_update().get(pk=submission.pk)
    if sub.status != CustomerPaymentSubmission.Status.AWAITING:
        raise ValueError(_("Only awaiting submissions can be rejected."))
    sub.status = CustomerPaymentSubmission.Status.REJECTED
    sub.rejected_by = user
    sub.rejected_at = timezone.now()
    sub.reject_reason = (reason or "").strip()
    sub.save(
        update_fields=[
            "status",
            "rejected_by",
            "rejected_at",
            "reject_reason",
        ]
    )
    audit_record(
        AuditAction.REJECT,
        sub,
        actor=user,
        changes={"reason": sub.reject_reason},
    )


@transaction.atomic
def record_customer_adjustment(
    *,
    customer: Customer,
    amount: Decimal,
    notes: str = "",
    user,
) -> CustomerLedger:
    """Manually move a customer's balance up or down without a sale or payment.

    Used for unclassified / legacy debt or to credit a customer outside the
    normal sales workflow. ``amount`` is signed:

    * positive -> raises balance (customer owes more).
    * negative -> lowers balance (treated like extra credit on file).

    The entry is intentionally invisible to volume / profit / loss reports —
    it only touches the customer ledger and ``current_balance``. Payments
    reduce ``current_balance`` and, via ``reapply_settlements_for_customer``,
    settle **manual / legacy debt first**: only payment headroom beyond that
    backlog is used to mark on-account sales as PAID (FIFO by sale date).
    """
    amt = Decimal(amount or 0)
    if amt == 0:
        raise ValueError(_("Adjustment amount must be non-zero."))

    customer_locked = Customer.objects.select_for_update().get(pk=customer.pk)
    entry = CustomerLedger.objects.create(
        customer=customer_locked,
        entry_type=CustomerLedger.EntryType.ADJUSTMENT,
        amount=amt,
        notes=(notes or "").strip(),
        created_by=user,
    )
    _apply_balance_delta(customer_locked, amt)
    audit_record(
        AuditAction.ADJUST,
        customer_locked,
        actor=user,
        changes={"amount": str(amt), "ledger_entry_id": entry.pk},
    )
    reapply_settlements_for_customer(
        customer=customer_locked, triggering_payment=None, user=user
    )
    return entry


@transaction.atomic
def write_off_customer_balance(*, customer: Customer, user) -> dict:
    """Close out a customer who will never pay: convert their unpaid
    on-account sales into a recorded loss and zero out their balance.

    Per unpaid sale (status=PENDING, on_account=True):

    * status -> WRITTEN_OFF (drops out of volume / profit aggregates).
    * loss_snapshot = cost_price_snapshot (cost shows up under losses).
    * A REVERSAL ledger row is added crediting the sale's sell price so
      the audit trail mirrors a normal cancellation.

    Any leftover positive balance on the customer (charges that were
    not tied to a still-pending on-account sale, e.g. legacy data) is
    cleared with an ADJUSTMENT entry so the running balance lands at 0.

    Finally the customer is marked inactive so future on-account sales
    will not flow into the account.

    Supplier balance is intentionally untouched: the goods were really
    delivered and the cost was really paid - that is exactly why we are
    booking it as a loss.

    Returns a small summary dict for UI feedback.
    """
    from sales.models import Sale  # local import to avoid app cycles

    customer_locked = Customer.objects.select_for_update().get(pk=customer.pk)
    starting_balance = Decimal(customer_locked.current_balance)

    unpaid = list(
        Sale.objects.select_for_update().filter(
            customer=customer_locked,
            on_account=True,
            status=Sale.Status.PENDING,
        )
    )

    sales_written_off = 0
    loss_total = Decimal("0")
    debt_cleared = Decimal("0")

    for sale in unpaid:
        cost = Decimal(sale.cost_price_snapshot)
        sell = Decimal(sale.sell_price_actual)

        sale.status = Sale.Status.WRITTEN_OFF
        sale.loss_snapshot = cost
        sale.save(
            update_fields=[
                "status",
                "loss_snapshot",
                "updated_at",
            ]
        )

        CustomerLedger.objects.create(
            customer=customer_locked,
            entry_type=CustomerLedger.EntryType.REVERSAL,
            amount=sell,
            sale=sale,
            created_by=user,
            notes="Sale written off as uncollectible loss.",
        )

        _apply_balance_delta(customer_locked, -sell)
        sales_written_off += 1
        loss_total += cost
        debt_cleared += sell

    customer_locked.refresh_from_db(fields=["current_balance"])
    leftover = Decimal(customer_locked.current_balance)
    if leftover > 0:
        CustomerLedger.objects.create(
            customer=customer_locked,
            entry_type=CustomerLedger.EntryType.ADJUSTMENT,
            amount=-leftover,
            created_by=user,
            notes="Account closed: residual debt written off.",
        )
        _apply_balance_delta(customer_locked, -leftover)
        debt_cleared += leftover

    if customer_locked.is_active:
        customer_locked.is_active = False
        customer_locked.save(update_fields=["is_active"])

    audit_record(
        AuditAction.WRITE_OFF,
        customer_locked,
        actor=user,
        changes={
            "sales_written_off": sales_written_off,
            "loss_total": str(loss_total),
            "debt_cleared": str(debt_cleared),
            "starting_balance": str(starting_balance),
        },
    )
    return {
        "sales_written_off": sales_written_off,
        "loss_total": loss_total,
        "debt_cleared": debt_cleared,
        "starting_balance": starting_balance,
    }


@transaction.atomic
def delete_customer_completely(*, customer: Customer, user) -> None:
    """Hard-delete a customer plus everything attached to them.

    Intended for QA / test cleanup. Walks every sale tied to the
    customer and runs ``delete_sale_permanently`` on each (so supplier
    balances and ledger reversals are handled correctly), then drops
    all remaining payments, payment submissions, and the customer row itself.
    Phones and ledger entries cascade automatically.
    """
    from sales.models import Sale  # local import to avoid app cycles
    from sales.services import delete_sale_permanently

    customer_locked = Customer.objects.select_for_update().get(pk=customer.pk)

    sale_ids = list(
        Sale.objects.filter(customer=customer_locked).values_list("id", flat=True)
    )
    for sid in sale_ids:
        sale = Sale.objects.select_for_update().get(pk=sid)
        delete_sale_permanently(sale=sale, user=user)

    CustomerPayment.objects.filter(customer=customer_locked).delete()
    CustomerLedger.objects.filter(customer=customer_locked).delete()
    CustomerPaymentSubmission.objects.filter(customer=customer_locked).delete()
    name_repr = customer_locked.name
    pk = customer_locked.pk
    customer_locked.delete()
    audit_record(
        AuditAction.DELETE,
        customer,
        actor=user,
        changes={"_repr": name_repr, "_pk": pk, "sales_purged": len(sale_ids)},
    )


@transaction.atomic
def delete_customer_payment(*, payment: CustomerPayment, user) -> None:
    """Remove a recorded customer payment and undo its effects.

    Reverses every sale this payment had FIFO-settled (status flips back
    to PENDING, payment_method/customer_payment cleared), drops the
    matching CustomerLedger PAYMENT row(s), refunds the customer's
    balance, deletes the payment, then re-runs FIFO so any remaining
    payments cover whatever charges they can.
    """
    from sales.models import Sale  # local import to avoid app-loading cycles

    locked = CustomerPayment.objects.select_for_update().get(pk=payment.pk)
    customer_locked = Customer.objects.select_for_update().get(pk=locked.customer_id)
    amount = Decimal(locked.amount)

    settled = list(
        Sale.objects.select_for_update().filter(
            customer=customer_locked,
            on_account=True,
            status=Sale.Status.PAID,
            customer_payment=locked,
        )
    )
    for s in settled:
        s.status = Sale.Status.PENDING
        s.paid_at = None
        s.paid_by = None
        s.payment_method = None
        s.customer_payment = None
        s.save(
            update_fields=[
                "status",
                "paid_at",
                "paid_by",
                "payment_method",
                "customer_payment",
                "updated_at",
            ]
        )

    CustomerLedger.objects.filter(customer=customer_locked, payment=locked).delete()
    _apply_balance_delta(customer_locked, amount)
    payment_id = locked.pk
    locked.delete()
    audit_record(
        AuditAction.DELETE,
        payment,
        actor=user,
        changes={
            "_pk": payment_id,
            "amount": str(amount),
            "customer_id": customer_locked.pk,
            "settled_sales_reverted": len(settled),
        },
    )

    reapply_settlements_for_customer(
        customer=customer_locked, triggering_payment=None, user=user
    )


@transaction.atomic
def delete_ledger_entry(*, entry: CustomerLedger, user) -> None:
    """Manually remove a single ledger row and undo its balance impact.

    Used to clean up orphan entries — e.g. a CHARGE row whose sale was
    deleted before we fixed delete_sale_permanently to also reverse the
    customer ledger. Signs the rollback the same way the original entry
    was signed when it was created (CHARGE/ADJUSTMENT add to balance,
    PAYMENT/REVERSAL subtract from balance).

    PAYMENT rows are refused unless the underlying CustomerPayment has
    already been deleted, because PAYMENT is the only way to settle on-
    account sales and silently dropping it would orphan a settlement.
    """
    locked = CustomerLedger.objects.select_for_update().get(pk=entry.pk)
    customer_locked = Customer.objects.select_for_update().get(pk=locked.customer_id)

    if locked.entry_type == CustomerLedger.EntryType.PAYMENT and locked.payment_id:
        raise ValueError(
            _(
                "Delete the payment from the customer's payment list instead — "
                "this ledger row is just its mirror entry."
            )
        )

    delta = Decimal("0")
    if locked.entry_type in (
        CustomerLedger.EntryType.CHARGE,
        CustomerLedger.EntryType.ADJUSTMENT,
    ):
        delta = -Decimal(locked.amount)
    elif locked.entry_type in (
        CustomerLedger.EntryType.PAYMENT,
        CustomerLedger.EntryType.REVERSAL,
    ):
        delta = Decimal(locked.amount)

    locked.delete()
    if delta != 0:
        _apply_balance_delta(customer_locked, delta)


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
    (on-account sales already PAID) for unallocated payment headroom.

    **Headroom first covers debt that is not tied to pending sale lines:**
    the greater of (a) cumulative net manual adjustment debits on the ledger
    (positive sum of ``ADJUSTMENT`` rows), and (b) ``current_balance`` minus
    the sum of *pending* on-account sale amounts (picks up orphan CHARGE /
    legacy slack). The remainder is applied oldest-first to PENDING on-account
    sales. Returns the number of sales newly settled.
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
    pending_sum = (
        Sale.objects.filter(
            customer=customer_locked,
            on_account=True,
            status=Sale.Status.PENDING,
        ).aggregate(s=Sum("sell_price_actual"))["s"]
        or Decimal("0")
    )
    balance = Decimal(customer_locked.current_balance)
    adj_sum = (
        CustomerLedger.objects.filter(
            customer=customer_locked,
            entry_type=CustomerLedger.EntryType.ADJUSTMENT,
        ).aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )
    # Opening balance / manual debits & credits (ledger adjustments) must be
    # covered by payments before any on-account sale is marked PAID. When the
    # only extra debt is from adjustments, ``current_balance - pending_sum``
    # after a payment can be zero while the adjustment total still reserves
    # headroom — so take the max of (net adjustment debt) and (slack in
    # balance vs pending sale face amounts) to catch orphan CHARGE rows too.
    ledger_legacy = max(Decimal("0"), Decimal(adj_sum))
    balance_slack = balance - pending_sum
    if balance_slack < 0:
        balance_slack = Decimal("0")
    non_sale_reserve = max(ledger_legacy, balance_slack)

    unallocated_payments = Decimal(payments_total) - Decimal(settled_total)
    available = unallocated_payments - non_sale_reserve
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
