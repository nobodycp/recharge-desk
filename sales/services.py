from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from companies.models import Company, Product
from sales.models import CompanyBalanceTransaction, PaymentMethod, Sale
from sales.pricing import effective_cost_for_product, loss_snapshot_for_sale


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
    payment_method: PaymentMethod,
    sell_price_actual: Decimal,
    notes: str,
    user,
    is_esim: bool = False,
) -> Sale:
    if product.line.company_id != company.id:
        raise ValueError("Product does not belong to the selected company.")
    if not company.is_active or not product.is_active:
        raise ValueError("Inactive company or product.")
    cost = effective_cost_for_product(product, is_esim=bool(is_esim))
    profit = sell_price_actual - cost
    loss_snap = loss_snapshot_for_sale(
        sell_price_actual=sell_price_actual,
        cost_price_snapshot=cost,
    )

    company_locked = Company.objects.select_for_update().get(pk=company.pk)

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
        status=Sale.Status.PENDING,
        created_by=user,
        notes=notes.strip(),
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
    return sale


@transaction.atomic
def update_sale_fields(
    *,
    sale: Sale,
    payment_method: PaymentMethod,
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

    Out of scope here: company / product / is_esim — those would require
    reversing and re-applying balance transactions and are best handled
    by cancelling and re-creating the sale.
    """
    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    cost = sale_locked.cost_price_snapshot

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
    return sale_locked


@transaction.atomic
def mark_sale_paid(*, sale: Sale, user) -> Sale:
    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale_locked.status != Sale.Status.PENDING:
        raise ValueError("Only pending sales can be marked as paid.")
    sale_locked.status = Sale.Status.PAID
    sale_locked.paid_at = timezone.now()
    sale_locked.paid_by = user
    sale_locked.save(update_fields=["status", "paid_at", "paid_by", "updated_at"])
    return sale_locked


def _cancellation_ledger_exists(sale_id: int) -> bool:
    return CompanyBalanceTransaction.objects.filter(
        entry_type=CompanyBalanceTransaction.EntryType.REVERSAL,
        reference_type=CompanyBalanceTransaction.ReferenceType.CANCELLATION,
        reference_id=sale_id,
    ).exists()


@transaction.atomic
def delete_sale_permanently(*, sale: Sale) -> None:
    """
    Remove a sale from the database and undo its effect on supplier balance.

    - Pending / paid: reverses the original cost deduction, then removes the sale ledger row.
    - Cancelled: removes both the original deduction and the cancellation reversal (net zero on balance).
    """
    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
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

    sale_locked.delete()


@transaction.atomic
def cancel_sale(*, sale: Sale, user) -> Sale:
    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale_locked.status == Sale.Status.CANCELLED:
        raise ValueError("Sale is already cancelled.")
    if _cancellation_ledger_exists(sale_locked.pk):
        raise ValueError("Cancellation already recorded for this sale.")

    company_locked = Company.objects.select_for_update().get(pk=sale_locked.company_id)
    cost = sale_locked.cost_price_snapshot

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

    sale_locked.status = Sale.Status.CANCELLED
    sale_locked.cancelled_at = timezone.now()
    sale_locked.cancelled_by = user
    sale_locked.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "updated_at",
        ]
    )
    return sale_locked


@transaction.atomic
def record_manual_deposit(*, company: Company, amount: Decimal, notes: str, user) -> CompanyBalanceTransaction:
    if amount <= 0:
        raise ValueError("Amount must be positive.")
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
