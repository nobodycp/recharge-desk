from django.apps import apps
from django.contrib import messages
from django.db import models, transaction
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from datetime import date

from accounts.permissions import management_required
from companies.forms import (
    CompanyForm,
    LayanReportReconcileForm,
    ProductLineForm,
    ProductVariantForm,
    SkyReportReconcileForm,
)
from companies.layan_reconcile import (
    company_supports_layan_reconcile,
    parse_pending_credits,
    reconcile_layan_report,
)
from companies.models import Company, Product, ProductLine
from companies.query_utils import apply_company_list_ordering
from companies.sky_reconcile import (
    company_supports_sky_reconcile,
    fetch_sky_rows_for_reconcile,
    reconcile_sky_report,
)
from core.pagination import paginate_request
from companies.statement import render_company_statement
from sales.services import initialize_company_opening_balance


def _has_sales_subquery(field_name: str):
    """Build an Exists() subquery on Sale that the listing pages can
    annotate so each row's `is_deletable` property reuses the result
    instead of firing a fresh EXISTS query per row.

    `field_name` is the column on Sale that points back to the row
    being listed (e.g. ``company_id``, ``product_id``,
    ``product__line_id``).
    """
    Sale = apps.get_model("sales", "Sale")
    return Exists(Sale.objects.filter(**{field_name: OuterRef("pk")}))


@management_required
def company_list(request):
    qs = Company.objects.annotate(
        has_sales_annotated=_has_sales_subquery("company_id"),
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(notes__icontains=q))
    qs = apply_company_list_ordering(request, qs)
    page_obj = paginate_request(request, qs)
    ctx = {
        "page_obj": page_obj,
        "title": _("Companies"),
        "sort": request.GET.get("sort") or "name",
        "order": (request.GET.get("order") or "asc").lower(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "companies/partials/company_list_results.html", ctx)
    return render(request, "companies/company_list.html", ctx)


@management_required
def company_create(request):
    form = CompanyForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        company = form.save()
        initialize_company_opening_balance(company=company, user=request.user)
        messages.success(request, _("Company saved."))
        return redirect("companies:company_list")
    return render(
        request,
        "companies/company_form.html",
        {"form": form, "title": _("New company")},
    )


@management_required
def company_detail(request, pk):
    company = get_object_or_404(Company, pk=pk)
    return render_company_statement(request, company)


@management_required
def layan_reconcile(request, pk):
    company = get_object_or_404(Company, pk=pk)
    if not company_supports_layan_reconcile(company):
        messages.error(request, _("Layan report matching is only available for Layan."))
        return redirect("companies:company_detail", pk=pk)

    form = LayanReportReconcileForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        try:
            min_settle = form.cleaned_data.get("min_settlement_difference")
            if min_settle is None:
                min_settle = 3
            result = reconcile_layan_report(
                company,
                form.cleaned_data["report_file"],
                period_from=form.cleaned_data.get("period_from"),
                period_to=form.cleaned_data.get("period_to"),
                pending_credits=parse_pending_credits(
                    form.cleaned_data.get("pending_credits") or ""
                ),
                min_settlement_difference=min_settle,
            )
            messages.success(
                request,
                _("Report processed (%(rows)s rows).") % {"rows": result.row_count},
            )
        except ImportError:
            messages.error(
                request,
                _("Excel support is not installed (openpyxl). Contact your administrator."),
            )
        except Exception as exc:
            messages.error(
                request,
                _("Could not read the report: %(error)s") % {"error": exc},
            )

    return render(
        request,
        "companies/layan_reconcile.html",
        {
            "company": company,
            "form": form,
            "result": result,
            "title": _("Layan report matching"),
        },
    )


@management_required
def sky_reconcile(request, pk):
    company = get_object_or_404(Company, pk=pk)
    if not company_supports_sky_reconcile(company):
        messages.error(request, _("Sky report matching is only available for Sky."))
        return redirect("companies:company_detail", pk=pk)

    initial = {}
    if request.method == "GET":
        # Sensible first range: full previous calendar month if today is early
        # in a month; otherwise leave blank — August 2026 preset for first run.
        initial = {
            "period_from": date(2026, 8, 1),
            "period_to": date(2026, 8, 31),
            "min_amount_diff": 3,
        }
    form = SkyReportReconcileForm(request.POST or None, initial=initial)
    result = None
    if request.method == "POST" and form.is_valid():
        period_from = form.cleaned_data["period_from"]
        period_to = form.cleaned_data["period_to"]
        min_diff = form.cleaned_data.get("min_amount_diff")
        if min_diff is None:
            min_diff = 3
        try:
            rows = fetch_sky_rows_for_reconcile(period_from, period_to)
            result = reconcile_sky_report(
                company,
                rows,
                period_from=period_from,
                period_to=period_to,
                min_amount_diff=min_diff,
            )
            messages.success(
                request,
                _("Sky report processed (%(rows)s rows).") % {"rows": result.row_count},
            )
        except Exception as exc:
            from phone_refresh.providers.sky_sales_client import SkySalesError

            if isinstance(exc, SkySalesError):
                messages.error(
                    request,
                    _("Could not fetch Sky report: %(error)s") % {"error": exc},
                )
            else:
                messages.error(
                    request,
                    _("Could not run Sky matching: %(error)s") % {"error": exc},
                )

    return render(
        request,
        "companies/sky_reconcile.html",
        {
            "company": company,
            "form": form,
            "result": result,
            "title": _("Sky report matching"),
        },
    )


@management_required
def company_edit(request, pk):
    company = get_object_or_404(Company, pk=pk)
    form = CompanyForm(request.POST or None, request.FILES or None, instance=company)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Company updated."))
        return redirect("companies:company_list")
    return render(
        request,
        "companies/company_form.html",
        {"form": form, "title": _("Edit company"), "company": company},
    )


@management_required
@transaction.atomic
def company_delete(request, pk):
    company = get_object_or_404(Company, pk=pk)
    if request.method != "POST":
        return redirect("companies:company_list")
    if not company.is_deletable:
        messages.error(
            request,
            _("Cannot delete a company that has sales. Cancel or archive sales first."),
        )
        return redirect("companies:company_list")
    name = company.name
    company.delete()
    messages.success(request, _("Company “%(name)s” was deleted.") % {"name": name})
    return redirect("companies:company_list")


@management_required
def product_list(request):
    Product = apps.get_model("companies", "Product")
    variants_qs = Product.objects.annotate(
        has_sales_annotated=_has_sales_subquery("product_id"),
    )
    lines = (
        ProductLine.objects.select_related("company", "default_package")
        .annotate(has_sales_annotated=_has_sales_subquery("product__line_id"))
        .prefetch_related(models.Prefetch("variants", queryset=variants_qs))
        .order_by("company__name", "sort_order", "name")
    )
    return render(
        request,
        "companies/product_list.html",
        {"lines": lines, "title": _("Products")},
    )


@management_required
def product_line_create(request):
    form = ProductLineForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        line = form.save()
        messages.success(request, _("Product line saved. Add one or more packages below."))
        return redirect("companies:product_variant_create", line_pk=line.pk)
    return render(
        request,
        "companies/product_line_form.html",
        {"form": form, "title": _("New product line")},
    )


@management_required
def product_line_edit(request, pk):
    line = get_object_or_404(ProductLine.objects.select_related("company"), pk=pk)
    form = ProductLineForm(request.POST or None, request.FILES or None, instance=line)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Product line updated."))
        return redirect("companies:product_list")
    return render(
        request,
        "companies/product_line_form.html",
        {"form": form, "title": _("Edit product line"), "line": line},
    )


@management_required
def product_variant_create(request, line_pk):
    line = get_object_or_404(ProductLine.objects.select_related("company"), pk=line_pk)
    form = ProductVariantForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        v = form.save(commit=False)
        v.line = line
        v.save()
        messages.success(request, _("Package saved."))
        return redirect("companies:product_list")
    return render(
        request,
        "companies/product_variant_form.html",
        {"form": form, "line": line, "title": _("New package")},
    )


@management_required
def product_variant_edit(request, pk):
    variant = get_object_or_404(Product.objects.select_related("line", "line__company"), pk=pk)
    form = ProductVariantForm(request.POST or None, request.FILES or None, instance=variant)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Package updated."))
        return redirect("companies:product_list")
    return render(
        request,
        "companies/product_variant_form.html",
        {"form": form, "line": variant.line, "variant": variant, "title": _("Edit package")},
    )


@management_required
@transaction.atomic
def product_line_delete(request, pk):
    line = get_object_or_404(ProductLine.objects.select_related("company"), pk=pk)
    if request.method != "POST":
        return redirect("companies:product_list")
    if not line.is_deletable:
        messages.error(
            request,
            _("Cannot delete this product line because it has sales recorded for one or more packages."),
        )
        return redirect("companies:product_list")
    label = str(line)
    line.delete()
    messages.success(request, _("Product line was deleted: %(label)s") % {"label": label})
    return redirect("companies:product_list")


@management_required
@transaction.atomic
def product_variant_delete(request, pk):
    variant = get_object_or_404(Product.objects.select_related("line"), pk=pk)
    if request.method != "POST":
        return redirect("companies:product_list")
    if not variant.is_deletable:
        messages.error(
            request,
            _("Cannot delete this package because it has sales recorded."),
        )
        return redirect("companies:product_list")
    label = variant.display_name
    variant.delete()
    messages.success(request, _("Package was deleted: %(label)s") % {"label": label})
    return redirect("companies:product_list")
