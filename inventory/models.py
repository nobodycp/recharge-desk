from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class SimStockBalance(models.Model):
    class Location(models.TextChoices):
        MAIN = "main", _("Main stock")
        CUSTOMER = "customer", _("Customer stock")

    location = models.CharField(
        _("location"),
        max_length=20,
        choices=Location.choices,
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.CASCADE,
        related_name="sim_stock_balances",
        verbose_name=_("customer"),
        null=True,
        blank=True,
    )
    product_line = models.ForeignKey(
        "companies.ProductLine",
        on_delete=models.PROTECT,
        related_name="sim_stock_balances",
        verbose_name=_("product line"),
    )
    quantity = models.PositiveIntegerField(_("quantity"), default=0)

    class Meta:
        verbose_name = _("SIM stock balance")
        verbose_name_plural = _("SIM stock balances")
        ordering = ["product_line__name", "customer__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["product_line"],
                condition=Q(location="main"),
                name="sim_balance_unique_main_per_line",
            ),
            models.UniqueConstraint(
                fields=["customer", "product_line"],
                condition=Q(location="customer"),
                name="sim_balance_unique_customer_per_line",
            ),
            models.CheckConstraint(
                check=(
                    Q(location="main", customer__isnull=True)
                    | Q(location="customer", customer__isnull=False)
                ),
                name="sim_balance_location_customer_consistent",
            ),
        ]

    def __str__(self):
        if self.location == self.Location.MAIN:
            return f"{self.product_line.name} (main): {self.quantity}"
        return f"{self.product_line.name} @ {self.customer}: {self.quantity}"


class SimStockMovement(models.Model):
    class MovementType(models.TextChoices):
        MAIN_RECEIVE = "main_receive", _("Main receive")
        ALLOCATE_TO_CUSTOMER = "allocate_to_customer", _("Allocate to customer")
        RETURN_FROM_CUSTOMER = "return_from_customer", _("Return from customer")
        ADJUSTMENT = "adjustment", _("Adjustment")
        DAMAGED = "damaged", _("Damaged")
        MANUAL_SALE = "manual_sale", _("Manual sale")
        SALE_CONSUME = "sale_consume", _("Sale consume")
        SALE_REVERSAL = "sale_reversal", _("Sale reversal")

    movement_type = models.CharField(
        _("movement type"),
        max_length=32,
        choices=MovementType.choices,
    )
    product_line = models.ForeignKey(
        "companies.ProductLine",
        on_delete=models.PROTECT,
        related_name="sim_stock_movements",
        verbose_name=_("product line"),
    )
    quantity = models.PositiveIntegerField(_("quantity"))
    from_balance = models.ForeignKey(
        SimStockBalance,
        on_delete=models.PROTECT,
        related_name="movements_out",
        verbose_name=_("from balance"),
        null=True,
        blank=True,
    )
    to_balance = models.ForeignKey(
        SimStockBalance,
        on_delete=models.PROTECT,
        related_name="movements_in",
        verbose_name=_("to balance"),
        null=True,
        blank=True,
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.SET_NULL,
        related_name="sim_stock_movements",
        verbose_name=_("customer"),
        null=True,
        blank=True,
    )
    sale = models.ForeignKey(
        "sales.Sale",
        on_delete=models.SET_NULL,
        related_name="sim_stock_movements",
        verbose_name=_("sale"),
        null=True,
        blank=True,
    )
    notes = models.TextField(_("notes"), blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sim_stock_movements_created",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("SIM stock movement")
        verbose_name_plural = _("SIM stock movements")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["movement_type", "-created_at"], name="sim_mov_type_created_idx"),
            models.Index(fields=["product_line", "-created_at"], name="sim_mov_line_created_idx"),
            models.Index(fields=["customer", "-created_at"], name="sim_mov_customer_created_idx"),
        ]

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.product_line.name} ×{self.quantity}"


class SimCard(models.Model):
    class Status(models.TextChoices):
        IN_MAIN = "in_main", _("In main stock")
        WITH_CUSTOMER = "with_customer", _("With customer")
        CONSUMED = "consumed", _("Consumed (sold)")
        DAMAGED = "damaged", _("Damaged")

    serial_or_iccid = models.CharField(
        _("serial or ICCID"),
        max_length=64,
        unique=True,
        null=True,
        blank=True,
    )
    product_line = models.ForeignKey(
        "companies.ProductLine",
        on_delete=models.PROTECT,
        related_name="sim_cards",
        verbose_name=_("product line"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.IN_MAIN,
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.SET_NULL,
        related_name="sim_cards",
        verbose_name=_("customer"),
        null=True,
        blank=True,
    )
    sale = models.ForeignKey(
        "sales.Sale",
        on_delete=models.SET_NULL,
        related_name="sim_cards",
        verbose_name=_("sale"),
        null=True,
        blank=True,
    )
    movement = models.ForeignKey(
        SimStockMovement,
        on_delete=models.SET_NULL,
        related_name="sim_cards",
        verbose_name=_("movement"),
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("SIM card")
        verbose_name_plural = _("SIM cards")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "product_line"], name="sim_card_status_line_idx"),
        ]

    def __str__(self):
        label = self.serial_or_iccid or f"#{self.pk}"
        return f"{label} ({self.get_status_display()})"
