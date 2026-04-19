from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.image_utils import maybe_optimize_image_field


class PaymentMethod(models.Model):
    name = models.CharField(_("name"), max_length=120, unique=True)
    icon = models.ImageField(
        _("icon"),
        upload_to="icons/payment_methods/",
        blank=True,
        null=True,
        help_text=_("Shown on the employee sales screen instead of the method name."),
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("payment method")
        verbose_name_plural = _("payment methods")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        maybe_optimize_image_field(self, "icon")
        super().save(*args, **kwargs)


class Sale(models.Model):
    class Status(models.TextChoices):
        AWAITING = "awaiting", _("Awaiting approval")
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        CANCELLED = "cancelled", _("Cancelled")

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.PROTECT,
        related_name="sales",
        verbose_name=_("company"),
    )
    product = models.ForeignKey(
        "companies.Product",
        on_delete=models.PROTECT,
        related_name="sales",
        verbose_name=_("product"),
    )
    reference_number = models.CharField(
        _("phone or shipment number"),
        max_length=64,
    )
    payer_name = models.CharField(_("payer name"), max_length=200)
    payment_method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.PROTECT,
        related_name="sales",
        verbose_name=_("payment method"),
        null=True,
        blank=True,
    )
    on_account = models.BooleanField(
        _("on account"),
        default=False,
        help_text=_("Customer credit sale: needs management approval and is settled later by a customer payment."),
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        related_name="sales",
        verbose_name=_("customer"),
        null=True,
        blank=True,
    )
    customer_payment = models.ForeignKey(
        "customers.CustomerPayment",
        on_delete=models.SET_NULL,
        related_name="settled_sales",
        verbose_name=_("settled by payment"),
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(_("approved at"), null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_approved",
        verbose_name=_("approved by"),
    )
    sell_price_actual = models.DecimalField(
        _("actual selling price"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    cost_price_snapshot = models.DecimalField(
        _("cost price (snapshot)"),
        max_digits=12,
        decimal_places=2,
    )
    profit_snapshot = models.DecimalField(
        _("profit (snapshot)"),
        max_digits=12,
        decimal_places=2,
    )
    loss_snapshot = models.DecimalField(
        _("Loss"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text=_(
            "When selling price is 0, equals supplier cost recorded as an explicit loss; otherwise 0."
        ),
    )
    is_esim = models.BooleanField(_("eSIM sale"), default=False)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sales_created",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    notes = models.TextField(_("notes"), blank=True)
    paid_at = models.DateTimeField(_("marked paid at"), null=True, blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_marked_paid",
        verbose_name=_("marked paid by"),
    )
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_cancelled",
        verbose_name=_("cancelled by"),
    )

    class Meta:
        verbose_name = _("sale")
        verbose_name_plural = _("sales")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference_number", "-created_at"], name="sale_ref_created_desc"),
        ]

    def __str__(self):
        return f"{self.reference_number} — {self.product.display_name}"


class CompanyBalanceTransaction(models.Model):
    class EntryType(models.TextChoices):
        DEPOSIT = "deposit", _("Deposit")
        DEDUCTION = "deduction", _("Deduction")
        ADJUSTMENT = "adjustment", _("Adjustment")
        REVERSAL = "reversal", _("Reversal")

    class ReferenceType(models.TextChoices):
        SALE = "sale", _("Sale")
        MANUAL = "manual", _("Manual")
        OPENING_BALANCE = "opening_balance", _("Opening balance")
        CANCELLATION = "cancellation", _("Cancellation")

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="balance_transactions",
        verbose_name=_("company"),
    )
    entry_type = models.CharField(
        _("type"),
        max_length=20,
        choices=EntryType.choices,
    )
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    reference_type = models.CharField(
        _("reference type"),
        max_length=32,
        choices=ReferenceType.choices,
    )
    reference_id = models.PositiveIntegerField(_("reference id"), null=True, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="balance_transactions_created",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("balance transaction")
        verbose_name_plural = _("balance transactions")
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.company.name} {self.entry_type} {self.amount}"
