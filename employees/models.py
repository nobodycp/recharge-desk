from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class EmployeeProfile(models.Model):
    """Payroll account for a staff user — salary accruals and sales cash held."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employee_profile",
        verbose_name=_("user"),
    )
    monthly_salary = models.DecimalField(
        _("monthly salary"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    current_balance = models.DecimalField(
        _("current balance"),
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
        help_text=_(
            "Positive: the shop owes the employee (salary / credits). "
            "Negative: the employee holds cash on behalf of the shop."
        ),
    )
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("employee")
        verbose_name_plural = _("employees")
        ordering = ["user__username"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self) -> str:
        profile = getattr(self.user, "profile", None)
        if profile and profile.display_name:
            return profile.display_name
        return self.user.get_username()


class EmployeeLedgerEntry(models.Model):
    """Signed ledger — positive credits the employee, negative debits them."""

    class EntryType(models.TextChoices):
        SALARY_ACCRUAL = "salary_accrual", _("Salary accrual")
        SALES_PAYMENT_RECEIVED = "sales_payment_received", _("Sales payment received")
        ADJUSTMENT = "adjustment", _("Adjustment")
        REVERSAL = "reversal", _("Reversal")

    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.CASCADE,
        related_name="ledger_entries",
        verbose_name=_("employee"),
    )
    entry_type = models.CharField(
        _("type"),
        max_length=32,
        choices=EntryType.choices,
    )
    amount = models.DecimalField(
        _("amount"),
        max_digits=14,
        decimal_places=2,
        help_text=_("Positive: shop owes employee. Negative: employee owes shop."),
    )
    salary_month = models.DateField(
        _("salary month"),
        null=True,
        blank=True,
        help_text=_("First day of the month for salary accrual rows."),
    )
    reference_sale = models.ForeignKey(
        "sales.Sale",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_ledger_entries",
        verbose_name=_("sale"),
    )
    payer_name = models.CharField(_("payer name"), max_length=200, blank=True)
    phone = models.CharField(_("phone or shipment number"), max_length=64, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_ledger_entries_created",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    expense = models.OneToOneField(
        "expenses.Expense",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="salary_ledger_entry",
        verbose_name=_("expense"),
    )

    class Meta:
        verbose_name = _("employee ledger entry")
        verbose_name_plural = _("employee ledger entries")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["employee", "-created_at"], name="emp_ledger_recent_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "salary_month"],
                condition=Q(entry_type="salary_accrual"),
                name="emp_salary_accrual_unique_month",
            ),
            models.UniqueConstraint(
                fields=["reference_sale"],
                condition=Q(entry_type="sales_payment_received"),
                name="emp_sale_payment_unique_sale",
            ),
        ]

    def __str__(self):
        return f"{self.employee} · {self.entry_type} {self.amount}"
