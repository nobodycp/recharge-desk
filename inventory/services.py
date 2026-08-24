"""SIM stock business logic — all writes go through this module."""

from __future__ import annotations

from typing import Literal, Optional, Union

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from audit.models import AuditAction
from audit.services import record as audit_record
from companies.models import ProductLine
from customers.models import Customer
from inventory.line_utils import canonical_product_line
from inventory.models import SimCard, SimStockBalance, SimStockMovement
from inventory.serial_utils import validate_serial_count
from sales.models import Sale

AMBIGUOUS = "ambiguous"
ResolveResult = Union[Customer, None, Literal["ambiguous"]]


def resolve_sim_customer(payer_name: str) -> ResolveResult:
    """Match payer name to a customer (trim + case-insensitive)."""
    name = (payer_name or "").strip()
    if not name:
        return None
    matches = list(Customer.objects.filter(name__iexact=name, is_active=True)[:2])
    if not matches:
        return None
    if len(matches) > 1:
        return AMBIGUOUS
    return matches[0]


def _stock_line(line: ProductLine) -> ProductLine:
    return canonical_product_line(line)


def _balance_qty(location: str, product_line: ProductLine, customer: Customer | None = None) -> int:
    line = _stock_line(product_line)
    qs = SimStockBalance.objects.filter(location=location, product_line=line)
    if location == SimStockBalance.Location.CUSTOMER:
        qs = qs.filter(customer=customer)
    else:
        qs = qs.filter(customer__isnull=True)
    return qs.aggregate(total=Sum("quantity"))["total"] or 0


def preview_sim_stock_deduction(*, payer_name: str, product_line: ProductLine) -> dict:
    """Estimate where a New SIM would be deducted (no stock mutation)."""
    line = _stock_line(product_line)
    line_label = line.name
    resolved = resolve_sim_customer(payer_name)
    if resolved == AMBIGUOUS:
        return {
            "source": "ambiguous",
            "quantity_available": 0,
            "message": str(
                _("Multiple customers share this payer name. Use a unique registered name.")
            ),
        }
    if isinstance(resolved, Customer):
        qty = _balance_qty(
            SimStockBalance.Location.CUSTOMER, line, customer=resolved
        )
        if qty < 1:
            return {
                "source": "no_customer_stock",
                "customer_id": resolved.pk,
                "customer_name": resolved.name,
                "quantity_available": 0,
                "message": str(
                    _("Customer “%(name)s” has no SIM stock for %(line)s.")
                    % {"name": resolved.name, "line": line_label}
                ),
            }
        return {
            "source": "customer",
            "customer_id": resolved.pk,
            "customer_name": resolved.name,
            "quantity_available": qty,
            "message": str(
                _("Will deduct from customer “%(name)s” stock (%(qty)s available).")
                % {"name": resolved.name, "qty": qty}
            ),
        }
    qty = _balance_qty(SimStockBalance.Location.MAIN, line)
    return {
        "source": "main",
        "quantity_available": qty,
        "message": str(
            _("Will deduct from main stock (%(qty)s available).") % {"qty": qty}
        ),
    }


def _get_balance_for_update(
    *,
    location: str,
    product_line: ProductLine,
    customer: Customer | None = None,
) -> SimStockBalance:
    line = _stock_line(product_line)
    lookup = {
        "location": location,
        "product_line": line,
        "defaults": {"quantity": 0},
    }
    if location == SimStockBalance.Location.MAIN:
        lookup["customer"] = None
    else:
        lookup["customer"] = customer
    balance, _created = SimStockBalance.objects.select_for_update().get_or_create(**lookup)
    return balance


def _register_cards(
    *,
    serials: list[str],
    product_line: ProductLine,
    status: str,
    movement: SimStockMovement,
    customer: Customer | None = None,
    sale: Sale | None = None,
) -> None:
    if not serials:
        return
    for serial in serials:
        if SimCard.objects.filter(serial_or_iccid__iexact=serial).exists():
            raise ValueError(
                _("Serial “%(serial)s” is already registered.") % {"serial": serial}
            )
        SimCard.objects.create(
            serial_or_iccid=serial,
            product_line=product_line,
            status=status,
            customer=customer,
            sale=sale,
            movement=movement,
        )


def _move_cards_to_customer(
    *,
    serials: list[str],
    product_line: ProductLine,
    customer: Customer,
    movement: SimStockMovement,
) -> None:
    for serial in serials:
        card = (
            SimCard.objects.select_for_update()
            .filter(
                serial_or_iccid__iexact=serial,
                product_line=product_line,
                status=SimCard.Status.IN_MAIN,
            )
            .first()
        )
        if card is None:
            raise ValueError(
                _("Serial “%(serial)s” is not registered in main stock for %(line)s.")
                % {"serial": serial, "line": product_line.name}
            )
        card.status = SimCard.Status.WITH_CUSTOMER
        card.customer = customer
        card.movement = movement
        card.save(update_fields=["status", "customer", "movement", "updated_at"])


def _move_cards_to_main(
    *,
    serials: list[str],
    product_line: ProductLine,
    movement: SimStockMovement,
) -> None:
    for serial in serials:
        card = SimCard.objects.select_for_update().get(
            serial_or_iccid__iexact=serial,
            product_line=product_line,
            status=SimCard.Status.WITH_CUSTOMER,
        )
        card.status = SimCard.Status.IN_MAIN
        card.customer = None
        card.movement = movement
        card.save(update_fields=["status", "customer", "movement", "updated_at"])


def _consume_card_for_sale(
    *,
    serial: str,
    product_line: ProductLine,
    deducted_from: str,
    customer: Customer | None,
    sale: Sale,
    movement: SimStockMovement,
) -> None:
    qs = SimCard.objects.select_for_update().filter(
        serial_or_iccid__iexact=serial.strip(),
        product_line=product_line,
    )
    if deducted_from == Sale.SimDeductedFrom.CUSTOMER:
        qs = qs.filter(status=SimCard.Status.WITH_CUSTOMER, customer=customer)
    else:
        qs = qs.filter(status=SimCard.Status.IN_MAIN)
    card = qs.first()
    if card is None:
        raise ValueError(
            _("Serial “%(serial)s” was not found in the expected stock location.")
            % {"serial": serial}
        )
    card.status = SimCard.Status.CONSUMED
    card.customer = customer
    card.sale = sale
    card.movement = movement
    card.save(update_fields=["status", "customer", "sale", "movement", "updated_at"])


def _reverse_card_for_sale(*, sale: Sale, movement: SimStockMovement) -> None:
    cards = list(SimCard.objects.select_for_update().filter(sale=sale))
    for card in cards:
        if sale.sim_deducted_from == Sale.SimDeductedFrom.CUSTOMER:
            card.status = SimCard.Status.WITH_CUSTOMER
        else:
            card.status = SimCard.Status.IN_MAIN
            card.customer = None
        card.sale = None
        card.movement = movement
        card.save(update_fields=["status", "customer", "sale", "movement", "updated_at"])


def _record_movement(
    *,
    movement_type: str,
    product_line: ProductLine,
    quantity: int,
    user,
    from_balance: SimStockBalance | None = None,
    to_balance: SimStockBalance | None = None,
    customer: Customer | None = None,
    sale: Sale | None = None,
    notes: str = "",
) -> SimStockMovement:
    line = _stock_line(product_line)
    movement = SimStockMovement.objects.create(
        movement_type=movement_type,
        product_line=line,
        quantity=quantity,
        from_balance=from_balance,
        to_balance=to_balance,
        customer=customer,
        sale=sale,
        notes=(notes or "").strip(),
        created_by=user,
    )
    audit_record(AuditAction.CREATE, movement, actor=user)
    return movement


@transaction.atomic
def receive_main_stock(
    *, product_line: ProductLine, qty: int, notes: str, user, serials: list[str] | None = None
) -> SimStockMovement:
    if qty < 1:
        raise ValueError(_("Quantity must be at least 1."))
    serials = serials or []
    validate_serial_count(serials=serials, qty=qty)
    line = _stock_line(product_line)
    balance = _get_balance_for_update(
        location=SimStockBalance.Location.MAIN, product_line=line
    )
    balance.quantity += qty
    balance.save(update_fields=["quantity"])
    movement = _record_movement(
        movement_type=SimStockMovement.MovementType.MAIN_RECEIVE,
        product_line=line,
        quantity=qty,
        user=user,
        to_balance=balance,
        notes=notes,
    )
    _register_cards(
        serials=serials,
        product_line=line,
        status=SimCard.Status.IN_MAIN,
        movement=movement,
    )
    return movement


@transaction.atomic
def allocate_to_customer(
    *,
    customer: Customer,
    product_line: ProductLine,
    qty: int,
    notes: str,
    user,
    serials: list[str] | None = None,
) -> SimStockMovement:
    if qty < 1:
        raise ValueError(_("Quantity must be at least 1."))
    serials = serials or []
    validate_serial_count(serials=serials, qty=qty)
    line = _stock_line(product_line)
    main = _get_balance_for_update(
        location=SimStockBalance.Location.MAIN, product_line=line
    )
    if main.quantity < qty:
        raise ValueError(_("Insufficient main stock for this product line."))
    cust = _get_balance_for_update(
        location=SimStockBalance.Location.CUSTOMER,
        product_line=line,
        customer=customer,
    )
    main.quantity -= qty
    cust.quantity += qty
    main.save(update_fields=["quantity"])
    cust.save(update_fields=["quantity"])
    movement = _record_movement(
        movement_type=SimStockMovement.MovementType.ALLOCATE_TO_CUSTOMER,
        product_line=line,
        quantity=qty,
        user=user,
        from_balance=main,
        to_balance=cust,
        customer=customer,
        notes=notes,
    )
    if serials:
        _move_cards_to_customer(
            serials=serials,
            product_line=line,
            customer=customer,
            movement=movement,
        )
    return movement


@transaction.atomic
def return_from_customer(
    *,
    customer: Customer,
    product_line: ProductLine,
    qty: int,
    notes: str,
    user,
    serials: list[str] | None = None,
) -> SimStockMovement:
    if qty < 1:
        raise ValueError(_("Quantity must be at least 1."))
    serials = serials or []
    validate_serial_count(serials=serials, qty=qty)
    line = _stock_line(product_line)
    cust = _get_balance_for_update(
        location=SimStockBalance.Location.CUSTOMER,
        product_line=line,
        customer=customer,
    )
    if cust.quantity < qty:
        raise ValueError(_("Insufficient customer stock for this product line."))
    main = _get_balance_for_update(
        location=SimStockBalance.Location.MAIN, product_line=line
    )
    cust.quantity -= qty
    main.quantity += qty
    cust.save(update_fields=["quantity"])
    main.save(update_fields=["quantity"])
    movement = _record_movement(
        movement_type=SimStockMovement.MovementType.RETURN_FROM_CUSTOMER,
        product_line=line,
        quantity=qty,
        user=user,
        from_balance=cust,
        to_balance=main,
        customer=customer,
        notes=notes,
    )
    if serials:
        _move_cards_to_main(serials=serials, product_line=line, movement=movement)
    return movement


def ensure_main_balance(product_line: ProductLine) -> SimStockBalance:
    """Get or create the main-stock row for a product line."""
    return _get_balance_for_update(
        location=SimStockBalance.Location.MAIN,
        product_line=_stock_line(product_line),
    )


@transaction.atomic
def set_balance_quantity(
    *,
    balance: SimStockBalance,
    new_quantity: int,
    reason: str,
    user,
    require_reason: bool = True,
) -> SimStockMovement | None:
    """Set absolute quantity (for testing / corrections / manual edits)."""
    reason = (reason or "").strip()
    if require_reason and not reason:
        raise ValueError(_("A reason is required."))
    if new_quantity < 0:
        raise ValueError(_("Quantity cannot be negative."))
    locked = SimStockBalance.objects.select_for_update().get(pk=balance.pk)
    old_qty = locked.quantity
    if new_quantity == old_qty:
        return None
    delta = new_quantity - old_qty
    locked.quantity = new_quantity
    locked.save(update_fields=["quantity"])
    if reason:
        notes = f"{reason} ({old_qty} → {new_quantity})"
    else:
        notes = str(_("Quantity edited (%(old)s → %(new)s)")) % {
            "old": old_qty,
            "new": new_quantity,
        }
    return _record_movement(
        movement_type=SimStockMovement.MovementType.ADJUSTMENT,
        product_line=locked.product_line,
        quantity=abs(delta),
        user=user,
        from_balance=locked if delta < 0 else None,
        to_balance=locked if delta > 0 else None,
        customer=locked.customer,
        notes=notes,
    )


@transaction.atomic
def clear_balance(*, balance: SimStockBalance, reason: str, user) -> SimStockMovement | None:
    """Zero out a balance row."""
    return set_balance_quantity(
        balance=balance,
        new_quantity=0,
        reason=reason or str(_("Cleared for testing")),
        user=user,
    )


@transaction.atomic
def delete_balance_row(*, balance: SimStockBalance, user) -> None:
    """Remove an empty balance row (testing cleanup)."""
    locked = SimStockBalance.objects.select_for_update().get(pk=balance.pk)
    if locked.quantity != 0:
        raise ValueError(_("Clear the balance before deleting it."))
    SimStockMovement.objects.filter(from_balance=locked).update(from_balance=None)
    SimStockMovement.objects.filter(to_balance=locked).update(to_balance=None)
    locked.delete()


@transaction.atomic
def delete_movement(*, movement: SimStockMovement, user) -> None:
    """Delete a movement log entry (does not reverse stock — testing only)."""
    if movement.movement_type in (
        SimStockMovement.MovementType.SALE_CONSUME,
        SimStockMovement.MovementType.SALE_REVERSAL,
    ):
        raise ValueError(
            _("Sale-linked movements cannot be deleted. Cancel the sale or adjust stock instead.")
        )
    movement.delete()


@transaction.atomic
def adjust_balance(
    *,
    balance: SimStockBalance,
    signed_delta: int,
    reason: str,
    user,
) -> SimStockMovement:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError(_("A reason is required for adjustments."))
    if signed_delta == 0:
        raise ValueError(_("Adjustment amount cannot be zero."))
    locked = SimStockBalance.objects.select_for_update().get(pk=balance.pk)
    new_qty = locked.quantity + signed_delta
    if new_qty < 0:
        raise ValueError(_("Adjustment would make stock negative."))
    locked.quantity = new_qty
    locked.save(update_fields=["quantity"])
    return _record_movement(
        movement_type=SimStockMovement.MovementType.ADJUSTMENT,
        product_line=locked.product_line,
        quantity=abs(signed_delta),
        user=user,
        from_balance=locked if signed_delta < 0 else None,
        to_balance=locked if signed_delta > 0 else None,
        customer=locked.customer,
        notes=reason,
    )


@transaction.atomic
def mark_damaged(
    *, balance: SimStockBalance, qty: int, notes: str, user
) -> SimStockMovement:
    if qty < 1:
        raise ValueError(_("Quantity must be at least 1."))
    locked = SimStockBalance.objects.select_for_update().get(pk=balance.pk)
    if locked.quantity < qty:
        raise ValueError(_("Insufficient stock to mark as damaged."))
    locked.quantity -= qty
    locked.save(update_fields=["quantity"])
    return _record_movement(
        movement_type=SimStockMovement.MovementType.DAMAGED,
        product_line=locked.product_line,
        quantity=qty,
        user=user,
        from_balance=locked,
        customer=locked.customer,
        notes=notes,
    )


@transaction.atomic
def record_manual_sale(
    *, balance: SimStockBalance, qty: int, notes: str, user
) -> SimStockMovement:
    """Deduct SIMs a customer sold outside the system (manual/offline sale)."""
    if qty < 1:
        raise ValueError(_("Quantity must be at least 1."))
    locked = SimStockBalance.objects.select_for_update().get(pk=balance.pk)
    if locked.quantity < qty:
        raise ValueError(_("Insufficient stock for a manual sale."))
    locked.quantity -= qty
    locked.save(update_fields=["quantity"])
    return _record_movement(
        movement_type=SimStockMovement.MovementType.MANUAL_SALE,
        product_line=locked.product_line,
        quantity=qty,
        user=user,
        from_balance=locked,
        customer=locked.customer,
        notes=notes,
    )


def _deduct_from_balance(*, balance: SimStockBalance, qty: int = 1) -> None:
    if balance.quantity < qty:
        raise ValueError(_("Insufficient SIM stock."))
    balance.quantity -= qty
    balance.save(update_fields=["quantity"])


def _add_to_balance(*, balance: SimStockBalance, qty: int = 1) -> None:
    balance.quantity += qty
    balance.save(update_fields=["quantity"])


@transaction.atomic
def consume_sim_for_sale(*, sale: Sale, user) -> Sale | None:
    """Deduct one SIM when management approves or marks paid. Idempotent."""
    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if not sale_locked.is_new_sim or sale_locked.sim_consumed_at is not None:
        return sale_locked

    product_line = _stock_line(sale_locked.product.line)
    resolved = resolve_sim_customer(sale_locked.payer_name)
    if resolved == AMBIGUOUS:
        raise ValueError(
            _("Multiple customers share the payer name “%(name)s”. Cannot deduct SIM stock.")
            % {"name": sale_locked.payer_name}
        )

    deducted_from = Sale.SimDeductedFrom.NONE
    from_balance = None

    if isinstance(resolved, Customer):
        balance = _get_balance_for_update(
            location=SimStockBalance.Location.CUSTOMER,
            product_line=product_line,
            customer=resolved,
        )
        if balance.quantity >= 1:
            _deduct_from_balance(balance=balance)
            deducted_from = Sale.SimDeductedFrom.CUSTOMER
            from_balance = balance
            movement_customer = resolved
        elif sale_locked.on_account and sale_locked.customer_id == resolved.pk:
            balance = _get_balance_for_update(
                location=SimStockBalance.Location.MAIN, product_line=product_line
            )
            if balance.quantity < 1:
                raise ValueError(
                    _("Insufficient main SIM stock for %(line)s.") % {"line": product_line.name}
                )
            _deduct_from_balance(balance=balance)
            deducted_from = Sale.SimDeductedFrom.MAIN
            from_balance = balance
            movement_customer = None
        else:
            raise ValueError(
                _("Customer “%(name)s” has no SIM stock for %(line)s.")
                % {"name": resolved.name, "line": product_line.name}
            )
    else:
        balance = _get_balance_for_update(
            location=SimStockBalance.Location.MAIN, product_line=product_line
        )
        if balance.quantity < 1:
            raise ValueError(
                _("Insufficient main SIM stock for %(line)s.") % {"line": product_line.name}
            )
        _deduct_from_balance(balance=balance)
        deducted_from = Sale.SimDeductedFrom.MAIN
        from_balance = balance
        movement_customer = None

    movement = _record_movement(
        movement_type=SimStockMovement.MovementType.SALE_CONSUME,
        product_line=product_line,
        quantity=1,
        user=user,
        from_balance=from_balance,
        customer=movement_customer,
        sale=sale_locked,
        notes="",
    )
    serial = (sale_locked.sim_serial_or_iccid or "").strip()
    if serial:
        _consume_card_for_sale(
            serial=serial,
            product_line=product_line,
            deducted_from=deducted_from,
            customer=movement_customer,
            sale=sale_locked,
            movement=movement,
        )
    sale_locked.sim_stock_movement = movement
    sale_locked.sim_consumed_at = timezone.now()
    sale_locked.sim_deducted_from = deducted_from
    sale_locked.save(
        update_fields=[
            "sim_stock_movement",
            "sim_consumed_at",
            "sim_deducted_from",
            "updated_at",
        ]
    )
    return sale_locked


@transaction.atomic
def reverse_sim_for_cancelled_sale(*, sale: Sale, user) -> None:
    """Restore SIM stock if it was consumed for this sale."""
    sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale_locked.sim_consumed_at is None:
        return

    product_line = _stock_line(sale_locked.product.line)
    qty = 1
    to_balance = None
    customer = None

    if sale_locked.sim_deducted_from == Sale.SimDeductedFrom.CUSTOMER:
        if sale_locked.sim_stock_movement_id and sale_locked.sim_stock_movement.customer_id:
            customer = sale_locked.sim_stock_movement.customer
        else:
            resolved = resolve_sim_customer(sale_locked.payer_name)
            if isinstance(resolved, Customer):
                customer = resolved
        if customer is None:
            raise ValueError(_("Cannot reverse SIM: customer stock source unknown."))
        to_balance = _get_balance_for_update(
            location=SimStockBalance.Location.CUSTOMER,
            product_line=product_line,
            customer=customer,
        )
    elif sale_locked.sim_deducted_from == Sale.SimDeductedFrom.MAIN:
        to_balance = _get_balance_for_update(
            location=SimStockBalance.Location.MAIN, product_line=product_line
        )
    else:
        return

    _add_to_balance(balance=to_balance, qty=qty)
    reversal = _record_movement(
        movement_type=SimStockMovement.MovementType.SALE_REVERSAL,
        product_line=product_line,
        quantity=qty,
        user=user,
        to_balance=to_balance,
        customer=customer,
        sale=sale_locked,
        notes="",
    )
    _reverse_card_for_sale(sale=sale_locked, movement=reversal)
    sale_locked.sim_consumed_at = None
    sale_locked.sim_deducted_from = Sale.SimDeductedFrom.NONE
    sale_locked.sim_stock_movement = None
    sale_locked.save(
        update_fields=[
            "sim_consumed_at",
            "sim_deducted_from",
            "sim_stock_movement",
            "updated_at",
        ]
    )


def consume_pending_new_sim_for_customer(*, customer: Customer, user) -> Sale | None:
    """FIFO: oldest pending New SIM sale for this customer."""
    sale = (
        Sale.objects.filter(
            customer=customer,
            is_new_sim=True,
            sim_consumed_at__isnull=True,
        )
        .exclude(status=Sale.Status.CANCELLED)
        .order_by("created_at")
        .first()
    )
    if sale is None:
        return None
    return consume_sim_for_sale(sale=sale, user=user)
