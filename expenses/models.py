from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class Expense(models.Model):
    title = models.CharField(_("title"), max_length=200)
    category = models.CharField(_("category"), max_length=120)
    amount = models.DecimalField(
        _("amount"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    date = models.DateField(_("date"))
    notes = models.TextField(_("notes"), blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="expenses_created",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("expense")
        verbose_name_plural = _("expenses")
        ordering = ["-date", "-id"]
        indexes = [
            # Expense report is always date-bucketed; ordering by date
            # also benefits from this index covering the default sort.
            models.Index(fields=["-date"], name="expense_date_desc_idx"),
            # Category drill-down in the expense report.
            models.Index(fields=["category", "-date"], name="expense_cat_date_idx"),
        ]

    def __str__(self):
        return self.title
