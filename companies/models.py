from decimal import Decimal

from django.apps import apps
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class Company(models.Model):
    name = models.CharField(_("name"), max_length=200)
    icon = models.ImageField(
        _("icon"),
        upload_to="icons/companies/",
        blank=True,
        null=True,
        help_text=_("Shown on the employee sales screen instead of the name."),
    )
    opening_balance = models.DecimalField(
        _("opening balance"),
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    current_balance = models.DecimalField(
        _("current balance"),
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
    )
    notes = models.TextField(_("notes"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("company")
        verbose_name_plural = _("companies")
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_deletable(self) -> bool:
        Sale = apps.get_model("sales", "Sale")
        return not Sale.objects.filter(company_id=self.pk).exists()


class ProductLine(models.Model):
    """Logical product family under a company (e.g. 019) — holds shared icon; packages are variants."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="product_lines",
        verbose_name=_("company"),
    )
    name = models.CharField(_("product line"), max_length=200)
    icon = models.ImageField(
        _("line icon"),
        upload_to="icons/product_lines/",
        blank=True,
        null=True,
        help_text=_("Default icon for all packages in this line; a package can override."),
    )
    sort_order = models.PositiveSmallIntegerField(_("sort order"), default=0)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("product line")
        verbose_name_plural = _("product lines")
        ordering = ["company", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="uniq_company_productline_name"),
        ]

    def __str__(self):
        return f"{self.company.name} — {self.name}"

    @property
    def is_deletable(self) -> bool:
        Sale = apps.get_model("sales", "Sale")
        return not Sale.objects.filter(product__line_id=self.pk).exists()


class Product(models.Model):
    """Sellable package / variant under a product line (e.g. 100 GB) with its own cost and default price."""

    line = models.ForeignKey(
        ProductLine,
        on_delete=models.CASCADE,
        related_name="variants",
        verbose_name=_("product line"),
    )
    variant_label = models.CharField(
        _("package"),
        max_length=120,
        blank=True,
        help_text=_('Examples: "100 GB", "200 GB". Leave empty if the line has only one package.'),
    )
    icon = models.ImageField(
        _("package icon"),
        upload_to="icons/products/",
        blank=True,
        null=True,
        help_text=_("Optional; falls back to the line icon on the employee screen."),
    )
    cost_price = models.DecimalField(
        _("cost price"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    default_sell_price = models.DecimalField(
        _("default sell price"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("product package")
        verbose_name_plural = _("product packages")
        ordering = ["line__company", "line__sort_order", "line__name", "variant_label"]

    @property
    def display_name(self) -> str:
        if self.variant_label:
            return f"{self.line.name} — {self.variant_label}"
        return self.line.name

    def effective_icon(self):
        if self.icon:
            return self.icon
        return self.line.icon if self.line_id else None

    @property
    def company_id(self) -> int:
        return self.line.company_id

    def __str__(self):
        return self.display_name

    @property
    def is_deletable(self) -> bool:
        Sale = apps.get_model("sales", "Sale")
        return not Sale.objects.filter(product_id=self.pk).exists()
