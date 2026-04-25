from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Customer(models.Model):
    """
    A debtor — typically a regular client topping up multiple SIMs on credit
    and settling later. Their `current_balance` is what they owe us:
    positive = owed to us, negative = credit on file (overpaid).
    """

    name = models.CharField(_("name"), max_length=200)
    notes = models.TextField(_("notes"), blank=True)
    current_balance = models.DecimalField(
        _("current balance"),
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
        help_text=_(
            "Positive: customer owes the shop. Negative: shop holds credit "
            "for the customer (overpayment)."
        ),
    )
    is_active = models.BooleanField(_("active"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="customers_created",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("customer")
        verbose_name_plural = _("customers")
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"], name="customer_name_idx"),
        ]

    def __str__(self):
        return self.name


class CustomerPhone(models.Model):
    """A phone (or shipment) number tied to a customer for fast lookup at sale entry."""

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="phones",
        verbose_name=_("customer"),
    )
    phone = models.CharField(_("phone or shipment number"), max_length=64)
    label = models.CharField(_("label"), max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("customer phone")
        verbose_name_plural = _("customer phones")
        ordering = ["customer__name", "phone"]
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "phone"],
                name="customer_phone_unique_per_customer",
            ),
        ]
        indexes = [
            models.Index(fields=["phone"], name="customer_phone_lookup_idx"),
        ]

    def __str__(self):
        return f"{self.customer.name} · {self.phone}"


class CustomerPayment(models.Model):
    """A real-money payment recorded against a customer's outstanding balance.

    The payment_method is the actual rail used (Bank of Palestine, Jawwal Pay,
    cash, ...) and is later propagated onto every sale this payment settles
    via the FIFO reapply pass, so volume / profit reports keep attributing
    money to the correct rail.
    """

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name=_("customer"),
    )
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    payment_method = models.ForeignKey(
        "sales.PaymentMethod",
        on_delete=models.PROTECT,
        related_name="customer_payments",
        verbose_name=_("payment method"),
    )
    notes = models.TextField(_("notes"), blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="customer_payments_recorded",
        verbose_name=_("recorded by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("customer payment")
        verbose_name_plural = _("customer payments")
        ordering = ["-created_at", "-id"]
        indexes = [
            # Customer detail page + reports list payments by recency.
            models.Index(fields=["customer", "-created_at"], name="cust_pay_recent_idx"),
            # Date-bucketed reports (today / week / month) hit this
            # column on its own without a customer filter.
            models.Index(fields=["-created_at"], name="cust_pay_created_desc"),
        ]

    def __str__(self):
        return f"{self.customer.name} · {self.amount}"


class CustomerPaymentSubmission(models.Model):
    """Employee-recorded customer payment awaiting management approval.

    Until approved, no CustomerPayment, ledger row, balance change, or FIFO
    settlement runs — see customers.services.approve_customer_payment_submission.
    """

    class Status(models.TextChoices):
        AWAITING = "awaiting", _("Awaiting approval")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="payment_submissions",
        verbose_name=_("customer"),
    )
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    payment_method = models.ForeignKey(
        "sales.PaymentMethod",
        on_delete=models.PROTECT,
        related_name="customer_payment_submissions",
        verbose_name=_("payment method"),
    )
    notes = models.TextField(_("notes"), blank=True)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.AWAITING,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="customer_payment_submissions_created",
        verbose_name=_("submitted by"),
    )
    created_at = models.DateTimeField(_("submitted at"), auto_now_add=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_payment_submissions_approved",
        verbose_name=_("approved by"),
    )
    approved_at = models.DateTimeField(_("approved at"), null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_payment_submissions_rejected",
        verbose_name=_("rejected by"),
    )
    rejected_at = models.DateTimeField(_("rejected at"), null=True, blank=True)
    reject_reason = models.TextField(_("reject reason"), blank=True)

    class Meta:
        verbose_name = _("customer payment submission")
        verbose_name_plural = _("customer payment submissions")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="cust_pay_sub_stat_idx"),
        ]

    def __str__(self):
        return f"{self.customer.name} · {self.amount} ({self.get_status_display()})"


class CustomerLedger(models.Model):
    """Append-only ledger — mirrors CompanyBalanceTransaction style.

    CHARGE rows raise customer.current_balance (created when management
    approves an on-account sale). PAYMENT rows reduce it. ADJUSTMENT and
    REVERSAL exist for manual corrections / undoing approvals.
    """

    class EntryType(models.TextChoices):
        CHARGE = "charge", _("Charge")
        PAYMENT = "payment", _("Payment")
        ADJUSTMENT = "adjustment", _("Adjustment")
        REVERSAL = "reversal", _("Reversal")

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="ledger_entries",
        verbose_name=_("customer"),
    )
    entry_type = models.CharField(
        _("type"),
        max_length=20,
        choices=EntryType.choices,
    )
    amount = models.DecimalField(
        _("amount"),
        max_digits=14,
        decimal_places=2,
        help_text=_("Always stored positive; entry_type signs it."),
    )
    sale = models.ForeignKey(
        "sales.Sale",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_ledger_entries",
        verbose_name=_("sale"),
    )
    payment = models.ForeignKey(
        CustomerPayment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
        verbose_name=_("payment"),
    )
    notes = models.TextField(_("notes"), blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_ledger_entries",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("customer ledger entry")
        verbose_name_plural = _("customer ledger entries")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["customer", "-created_at"], name="cust_ledger_recent_idx"),
        ]

    def __str__(self):
        return f"{self.customer.name} · {self.entry_type} {self.amount}"
