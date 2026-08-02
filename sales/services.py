from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from audit.models import AuditAction
from audit.services import diff_fields, record as audit_record, snapshot
from companies.models import Company, Product
from sales.models import CompanyBalanceTransaction, PaymentMethod, Sale
from sales.pricing import effective_cost_for_product, loss_snapshot_for_sale


_SALE_AUDITED_FIELDS = (
    "reference_number",
    "payer_name",
    "sell_price_actual",
    "payment_method_id",
    "notes",
    "status",
    "on_account",
    "customer_id",
)


def _apply_balance_delta(company: Company, delta: Decimal) -> None:
    Company.objects.filter(pk=company.pk).update(current_balance=F("current_balance") + delta)
    company.refresh_from_db(fields=["current_balance"])


@transaction.atomic
def create_sale(
    *,
    company: Company,
    product: Product,
    reference_number: str,
    payer_name: str,
    payment_method: Optional[PaymentMethod],
    sell_price_actual: Decimal,
    notes: str,
    user,
    is_esim: bool = False,
    is_new_sim: bool = False,
    sim_serial_or_iccid: str = "",
    on_account: bool = False,
    customer=None,
    paid_via_employee: bool = False,
    employee_recipient=None,
) -> Sale:
    if product.line.company_id != company.id:
        raise ValueError(_("Product does not belong to the selected company."))
    if not company.is_active or not product.is_active:
        raise ValueError(_("Inactive company or product."))
    if on_account and paid_via_employee:
        raise ValueError(_("A sale cannot be both on-account and paid via employee."))
    if paid_via_employee:
        if employee_recipient is None:
            raise ValueError(_("Employee payment sales require an employee recipient."))
        if payment_method is not None:
            raise ValueError(_("Employee payment sales must not have a payment method."))
    elif on_account:
        if customer is None:
            raise ValueError(_("On-account sales require a customer."))
        if payment_method is not None:
            raise ValueError(_("On-account sales must not have a payment method at entry time."))
    else:
        if payment_method is None:
            raise ValueError(_("Payment method is required for non on-account sales."))
    cost = effective_cost_for_product(product, is_esim=bool(is_esim))
    profit = sell_price_actual - cost
    loss_snap = loss_snapshot_for_sale(
        sell_price_actual=sell_price_actual,
        cost_price_snapshot=cost,
    )

    company_locked = Company.objects.select_for_update().get(pk=company.pk)

    initial_status = Sale.Status.AWAITING if on_account else Sale.Status.PENDING

    sale = Sale.objects.create(
        company=company_locked,
        product=product,
        reference_number=reference_number.strip(),
        payer_name=payer_name.strip(),
        payment_method=payment_method,
        sell_price_actual=sell_price_actual,
        cost_price_snapshot=cost,
        profit_snapshot=profit,
        loss_snapshot=loss_snap,
        is_esim=bool(is_esim),
        is_new_sim=bool(is_new_sim),
        sim_serial_or_iccid=(sim_serial_or_iccid or "").strip() if is_new_sim else "",
        status=initial_status,
        created_by=user,
        notes=notes.strip(),
        on_account=bool(on_account),
        customer=customer if on_account else None,
        paid_via_employee=bool(paid_via_employee),
        employee_recipient=employee_recipient if paid_via_employee else None,
    )

    CompanyBalanceTransaction.objects.create(
        company=company_locked,
        entry_type=CompanyBalanceTransaction.EntryType.DEDUCTION,
        amount=cost,
        reference_type=CompanyBalanceTransaction.ReferenceType.SALE,
        reference_id=sale.pk,
        notes="",
        created_by=user,
    )
    _apply_balance_delta(company_locked, -cost)
    audit_record(
        AuditAction.CREATE,
        sale,
        actor=user,
        changes=diff_fields(None, snapshot(sale, _SALE_AUDITED_FIELDS)),
    )
    return sale


@transaction.atomic
def update_sale_fields(
    *,
    sale: Sale,
    payment_method: Optional[PaymentMethod],
    payer_name: str,
    reference_number: str,
    sell_price_actual: Decimal,
    notes: str,
    user,
) -> Sale:
    """
    Edit safe fields on an existing sale — the ones that do NOT change the
    company's supplier-balance ledger (i.e. they leave cost_price_snapshot
    and the original deduction untouched).

    Editable: payment_method, payer_name, reference_number, sell_price_actual,
              notes. Profit and loss snapshots are recomputed from the new
              selling price against the original cost snapshot.

    For posted on-account sales, the customer CHARGE ledger row and
    ``customer.current_balance`` are synced to the new selling price.

    Out of scope here: company / product / is_esim — those would require
    reversing and re-applying balance transactions and are best handled
    by cancelling and re-creating the sale.
    """
    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    before = snapshot(sale_locked, _SALE_AUDITED_FIELDS)
    cost = sale_locked.cost_price_snapshot

    if sale_locked.paid_via_employee or sale_locked.on_account:
        sale_locked.payment_method = None
    elif payment_method is None:
        raise ValueError(_("Payment method is required for this sale."))
    else:
        sale_locked.payment_method = payment_method
    sale_locked.payer_name = (payer_name or "").strip()
    sale_locked.reference_number = (reference_number or "").strip()
    sale_locked.sell_price_actual = sell_price_actual
    sale_locked.profit_snapshot = sell_price_actual - cost
    sale_locked.loss_snapshot = loss_snapshot_for_sale(
        sell_price_actual=sell_price_actual,
        cost_price_snapshot=cost,
    )
    sale_locked.notes = (notes or "").strip()
    sale_locked.save(
        update_fields=[
            "payment_method",
            "payer_name",
            "reference_number",
            "sell_price_actual",
            "profit_snapshot",
            "loss_snapshot",
            "notes",
            "updated_at",
        ]
    )
    if sale_locked.on_account and sale_locked.customer_id:
        from customers.services import sync_on_account_charge_for_sale

        sync_on_account_charge_for_sale(sale=sale_locked, user=user)
        sale_locked.refresh_from_db()
    changes = diff_fields(before, snapshot(sale_locked, _SALE_AUDITED_FIELDS))
    if changes:
        audit_record(AuditAction.UPDATE, sale_locked, actor=user, changes=changes)
    return sale_locked


@transaction.atomic
def mark_sale_paid(*, sale: Sale, user) -> Sale:
    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale_locked.status != Sale.Status.PENDING:
        raise ValueError(_("Only pending sales can be marked as paid."))
    sale_locked.status = Sale.Status.PAID
    sale_locked.paid_at = timezone.now()
    sale_locked.paid_by = user
    sale_locked.save(update_fields=["status", "paid_at", "paid_by", "updated_at"])
    audit_record(AuditAction.MARK_PAID, sale_locked, actor=user)
    from inventory.services import consume_sim_for_sale

    consume_sim_for_sale(sale=sale_locked, user=user)
    if sale_locked.paid_via_employee and sale_locked.employee_recipient_id:
        from employees.services import record_sales_payment_received

        record_sales_payment_received(sale=sale_locked, user=user)
    return sale_locked


def _cancellation_ledger_exists(sale_id: int) -> bool:
    return CompanyBalanceTransaction.objects.filter(
        entry_type=CompanyBalanceTransaction.EntryType.REVERSAL,
        reference_type=CompanyBalanceTransaction.ReferenceType.CANCELLATION,
        reference_id=sale_id,
    ).exists()


@transaction.atomic
def delete_sale_permanently(*, sale: Sale, user=None) -> None:
    """
    Remove a sale from the database and undo every side-effect it had.

    - Supplier balance: reverses the original cost deduction (and any
      cancellation reversal, so the company balance lands net-zero).
    - Customer ledger: for an on-account sale, undoes any CHARGE rows
      (debits) and any REVERSAL rows (credits) that pointed at this sale,
      so the customer's running balance also lands net-zero.
    """
    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    audit_snapshot = snapshot(sale_locked, _SALE_AUDITED_FIELDS)
    audit_repr = str(sale_locked)
    audit_pk = sale_locked.pk
    company_locked = Company.objects.select_for_update().get(pk=sale_locked.company_id)
    sale_id = sale_locked.pk

    txns = list(
        CompanyBalanceTransaction.objects.filter(
            company=company_locked,
            reference_id=sale_id,
            reference_type__in=[
                CompanyBalanceTransaction.ReferenceType.SALE,
                CompanyBalanceTransaction.ReferenceType.CANCELLATION,
            ],
        ).order_by("id")
    )

    for txn in txns:
        if txn.entry_type == CompanyBalanceTransaction.EntryType.DEDUCTION:
            _apply_balance_delta(company_locked, txn.amount)
        elif txn.entry_type == CompanyBalanceTransaction.EntryType.REVERSAL:
            if txn.reference_type == CompanyBalanceTransaction.ReferenceType.CANCELLATION:
                _apply_balance_delta(company_locked, -txn.amount)

    if txns:
        CompanyBalanceTransaction.objects.filter(pk__in=[t.pk for t in txns]).delete()

    from inventory.services import reverse_sim_for_cancelled_sale

    reverse_sim_for_cancelled_sale(sale=sale_locked, user=user)

    if sale_locked.paid_via_employee and sale_locked.employee_recipient_id:
        from employees.services import reverse_sales_payment_for_sale

        reverse_sales_payment_for_sale(sale=sale_locked)

    if sale_locked.on_account and sale_locked.customer_id:
        from customers.models import Customer, CustomerLedger

        customer_locked = Customer.objects.select_for_update().get(pk=sale_locked.customer_id)
        ledger_rows = list(
            CustomerLedger.objects.filter(customer=customer_locked, sale=sale_locked).order_by("id")
        )
        delta = Decimal("0")
        for row in ledger_rows:
            if row.entry_type == CustomerLedger.EntryType.CHARGE:
                delta -= row.amount
            elif row.entry_type == CustomerLedger.EntryType.REVERSAL:
                delta += row.amount
        if ledger_rows:
            CustomerLedger.objects.filter(pk__in=[r.pk for r in ledger_rows]).delete()
        if delta != 0:
            Customer.objects.filter(pk=customer_locked.pk).update(
                current_balance=F("current_balance") + delta
            )

    sale_locked.delete()
    # Even though the row is gone the audit snapshot keeps a textual
    # description + the field-level diff, so a deleted sale still has
    # an investigable trail.
    audit_record(
        AuditAction.DELETE,
        sale,  # original instance carries pk + str() for the snapshot
        actor=user,
        changes={"_snapshot": audit_snapshot, "_repr": audit_repr, "_pk": audit_pk},
    )


def find_orphan_sale_balance_transactions(*, company: Optional[Company] = None):
    """Return the queryset of CompanyBalanceTransaction rows whose linked
    Sale no longer exists.

    Such rows leak in two ways:

    * A Sale was hard-deleted by the management UI before the
      ``delete_sale_permanently`` ledger reversal landed (an early bug
      we patched).
    * Manual ``DELETE`` on the Sale row from a shell or admin without
      using the helper.

    Each orphan is identified by ``reference_type`` in (SALE, CANCELLATION)
    and a ``reference_id`` that doesn't resolve to an existing Sale.
    """
    qs = CompanyBalanceTransaction.objects.filter(
        reference_type__in=[
            CompanyBalanceTransaction.ReferenceType.SALE,
            CompanyBalanceTransaction.ReferenceType.CANCELLATION,
        ],
        reference_id__isnull=False,
    )
    if company is not None:
        qs = qs.filter(company=company)
    existing_sale_ids = set(
        Sale.objects.filter(pk__in=qs.values_list("reference_id", flat=True))
        .values_list("pk", flat=True)
    )
    return qs.exclude(reference_id__in=existing_sale_ids)


@transaction.atomic
def cleanup_orphan_sale_balance_transactions(
    *, user=None, dry_run: bool = False, company: Optional[Company] = None
) -> dict:
    """Delete orphan SALE/CANCELLATION ledger rows and refund the company.

    For every affected company we sum the net balance impact of the orphan
    rows (DEDUCTIONs subtracted from balance, REVERSALs / DEPOSITs added)
    and post a single MANUAL REVERSAL/ADJUSTMENT entry that cancels it
    out, then delete the orphan rows. This keeps the running balance
    accurate while leaving a single audit row that says "cleanup of N
    orphan sale references" for traceability.

    Returns a summary dict::

        {
          "orphan_count": int,
          "companies_affected": int,
          "net_refund_total": Decimal,  # positive = balance restored
          "dry_run": bool,
        }
    """
    orphans = list(find_orphan_sale_balance_transactions(company=company))
    summary = {
        "orphan_count": len(orphans),
        "companies_affected": 0,
        "net_refund_total": Decimal("0"),
        "dry_run": dry_run,
    }
    if not orphans:
        return summary

    by_company: dict[int, list[CompanyBalanceTransaction]] = {}
    for txn in orphans:
        by_company.setdefault(txn.company_id, []).append(txn)

    summary["companies_affected"] = len(by_company)

    for company_id, txns in by_company.items():
        delta = Decimal("0")
        for txn in txns:
            if txn.entry_type == CompanyBalanceTransaction.EntryType.DEDUCTION:
                delta += txn.amount
            elif txn.entry_type in (
                CompanyBalanceTransaction.EntryType.REVERSAL,
                CompanyBalanceTransaction.EntryType.DEPOSIT,
                CompanyBalanceTransaction.EntryType.ADJUSTMENT,
            ):
                delta -= txn.amount
        summary["net_refund_total"] += delta

        if dry_run:
            continue

        company_locked = Company.objects.select_for_update().get(pk=company_id)
        ids = [t.pk for t in txns]
        if delta != 0:
            CompanyBalanceTransaction.objects.create(
                company=company_locked,
                entry_type=(
                    CompanyBalanceTransaction.EntryType.REVERSAL
                    if delta > 0
                    else CompanyBalanceTransaction.EntryType.ADJUSTMENT
                ),
                amount=abs(delta) if delta > 0 else delta,
                reference_type=CompanyBalanceTransaction.ReferenceType.MANUAL,
                reference_id=None,
                notes=(
                    f"Cleanup of {len(txns)} orphan sale ledger row(s) "
                    f"(refs: {', '.join(str(t.reference_id) for t in txns)})."
                )[:500],
                created_by=user,
            )
            _apply_balance_delta(company_locked, delta)
        CompanyBalanceTransaction.objects.filter(pk__in=ids).delete()

    return summary


@transaction.atomic
def cancel_sale(*, sale: Sale, user) -> Sale:
    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale_locked.status == Sale.Status.CANCELLED:
        raise ValueError(_("Sale is already cancelled."))
    if _cancellation_ledger_exists(sale_locked.pk):
        raise ValueError(_("Cancellation already recorded for this sale."))

    company_locked = Company.objects.select_for_update().get(pk=sale_locked.company_id)
    cost = sale_locked.cost_price_snapshot
    previous_status = sale_locked.status

    CompanyBalanceTransaction.objects.create(
        company=company_locked,
        entry_type=CompanyBalanceTransaction.EntryType.REVERSAL,
        amount=cost,
        reference_type=CompanyBalanceTransaction.ReferenceType.CANCELLATION,
        reference_id=sale_locked.pk,
        notes="",
        created_by=user,
    )
    _apply_balance_delta(company_locked, cost)

    # If this was an on-account sale that had already been approved (i.e. a
    # CHARGE row is sitting in the customer ledger), reverse it so the
    # customer's running balance reflects the cancellation. AWAITING sales
    # have not posted a charge yet, so nothing to reverse there.
    if (
        sale_locked.on_account
        and sale_locked.customer_id
        and previous_status != Sale.Status.AWAITING
    ):
        from customers.models import Customer, CustomerLedger

        customer_locked = Customer.objects.select_for_update().get(pk=sale_locked.customer_id)
        CustomerLedger.objects.create(
            customer=customer_locked,
            entry_type=CustomerLedger.EntryType.REVERSAL,
            amount=sale_locked.sell_price_actual,
            sale=sale_locked,
            created_by=user,
            notes="Sale cancelled.",
        )
        Customer.objects.filter(pk=customer_locked.pk).update(
            current_balance=F("current_balance") - sale_locked.sell_price_actual
        )

    sale_locked.status = Sale.Status.CANCELLED
    sale_locked.cancelled_at = timezone.now()
    from inventory.services import reverse_sim_for_cancelled_sale

    reverse_sim_for_cancelled_sale(sale=sale_locked, user=user)

    sale_locked.cancelled_by = user
    sale_locked.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "updated_at",
        ]
    )
    audit_record(
        AuditAction.CANCEL,
        sale_locked,
        actor=user,
        changes={"previous_status": previous_status},
    )
    return sale_locked


@transaction.atomic
def record_manual_deposit(*, company: Company, amount: Decimal, notes: str, user) -> CompanyBalanceTransaction:
    if amount <= 0:
        raise ValueError(_("Amount must be positive."))
    company_locked = Company.objects.select_for_update().get(pk=company.pk)
    txn = CompanyBalanceTransaction.objects.create(
        company=company_locked,
        entry_type=CompanyBalanceTransaction.EntryType.DEPOSIT,
        amount=amount,
        reference_type=CompanyBalanceTransaction.ReferenceType.MANUAL,
        reference_id=None,
        notes=notes.strip(),
        created_by=user,
    )
    _apply_balance_delta(company_locked, amount)
    return txn


@transaction.atomic
def record_balance_adjustment(*, company: Company, signed_amount: Decimal, notes: str, user) -> CompanyBalanceTransaction:
    company_locked = Company.objects.select_for_update().get(pk=company.pk)
    txn = CompanyBalanceTransaction.objects.create(
        company=company_locked,
        entry_type=CompanyBalanceTransaction.EntryType.ADJUSTMENT,
        amount=signed_amount,
        reference_type=CompanyBalanceTransaction.ReferenceType.MANUAL,
        reference_id=None,
        notes=notes.strip(),
        created_by=user,
    )
    _apply_balance_delta(company_locked, signed_amount)
    return txn


@transaction.atomic
def initialize_company_opening_balance(*, company: Company, user) -> None:
    """Idempotent: creates opening-balance ledger row if missing."""
    exists = CompanyBalanceTransaction.objects.filter(
        company=company,
        reference_type=CompanyBalanceTransaction.ReferenceType.OPENING_BALANCE,
        reference_id=company.pk,
    ).exists()
    if exists:
        return
    ob = company.opening_balance
    if ob == 0:
        return
    company_locked = Company.objects.select_for_update().get(pk=company.pk)
    CompanyBalanceTransaction.objects.create(
        company=company_locked,
        entry_type=CompanyBalanceTransaction.EntryType.DEPOSIT,
        amount=ob,
        reference_type=CompanyBalanceTransaction.ReferenceType.OPENING_BALANCE,
        reference_id=company_locked.pk,
        notes="",
        created_by=user,
    )
    _apply_balance_delta(company_locked, ob)
