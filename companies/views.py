import logging
from itertools import groupby

from django.apps import apps
from django.contrib import messages
from django.db import models, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import management_required
from companies.forms import (
    CompanyForm,
    CompanySearchForm,
    LayanReportReconcileForm,
    ProductLineForm,
    ProductSearchForm,
    ProductVariantForm,
    SkyReportReconcileForm,
)
from companies.models import Company, Product, ProductLine
from companies.query_utils import apply_company_list_ordering
from core.pagination import paginate_request
from providers import operations as provider_operations
from providers import service as provider_service
from providers.contracts import (
    RECONCILE_NOT_SUPPORTED,
    AuthRequired,
    Capability,
    PermanentError,
    TransientError,
)
from companies import connection
from companies.statement import render_company_statement
from sales.services import initialize_company_opening_balance

log = logging.getLogger(__name__)

_SKY_TIMEOUT_CODES = frozenset({"timeout", "sync_budget_exceeded"})


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
        "card_form": CompanySearchForm(initial=request.GET.dict()),
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
@require_POST
def company_connection_system(request, pk):
    return connection.save_system(request, get_object_or_404(Company, pk=pk))


@management_required
@require_POST
def company_connection_services(request, pk):
    return connection.save_services(request, get_object_or_404(Company, pk=pk))


@management_required
@require_POST
def company_connection_account(request, pk):
    return connection.save_account(request, get_object_or_404(Company, pk=pk))


@management_required
@require_POST
def company_connection_test(request, pk):
    return connection.start_login_test(request, get_object_or_404(Company, pk=pk))


@management_required
@require_GET
def company_connection_status(request, pk):
    return connection.login_status(request, get_object_or_404(Company, pk=pk))


def _reconcile_state(company, provider_key: str):
    """Each reconcile page serves only its own provider's companies."""
    return provider_service.service_state(
        company, Capability.RECONCILE, provider=provider_key
    )


def _supports_reconcile(company, provider_key: str) -> bool:
    return _reconcile_state(company, provider_key).code == "active"


def _reconcile_blocked_message(company, provider_key: str, unsupported: str):
    """Flash text when the reconcile page is closed to ``company``, else ``None``.

    A company of the right provider whose service is off or not configured is
    told why; "only available for X" is kept for truly unsupported ones.
    """
    state = _reconcile_state(company, provider_key)
    if state.code == "active":
        return None
    return unsupported if state.code == "unsupported" else state.reason


@management_required
def layan_reconcile(request, pk):
    company = get_object_or_404(Company, pk=pk)
    blocked = _reconcile_blocked_message(
        company, "layan", _("Layan report matching is only available for Layan.")
    )
    if blocked:
        messages.error(request, blocked)
        return redirect("companies:company_detail", pk=pk)

    form = LayanReportReconcileForm(request.POST or None, request.FILES or None)
    result = None
    template = "companies/layan_reconcile.html"
    if request.method == "POST" and form.is_valid():
        try:
            min_settle = form.cleaned_data.get("min_settlement_difference")
            if min_settle is None:
                min_settle = 3
            result, template = provider_service.reconcile(
                company,
                form.cleaned_data.get("period_from"),
                form.cleaned_data.get("period_to"),
                workbook=form.cleaned_data["report_file"],
                min_amount_diff=min_settle,
                pending_credits_text=form.cleaned_data.get("pending_credits") or "",
                user_id=request.user.pk,
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
            if isinstance(exc, PermanentError) and str(exc) == RECONCILE_NOT_SUPPORTED:
                messages.error(
                    request, _("Layan report matching is only available for Layan.")
                )
                return redirect("companies:company_detail", pk=pk)
            log.exception("Layan report matching failed for company %s", company.pk)
            messages.error(
                request,
                _(
                    "Could not read the file. Make sure it is the Layan charges "
                    "report exported as Excel (.xlsx)."
                ),
            )

    return render(
        request,
        template,
        {
            "company": company,
            "form": form,
            "result": result,
            "provider": "layan",
            "supplier_label": _("Layan"),
            "title": _("Layan report matching"),
        },
    )


def _sky_error_message(company, exc: Exception):
    """Plain-language text for a failed Sky run; unexpected errors go to the log."""
    code = str(exc)
    if isinstance(exc, TransientError) and code in _SKY_TIMEOUT_CODES:
        return _(
            "Sky took too long to answer. Try a shorter period or run it again "
            "in a minute."
        )
    if isinstance(exc, TransientError) and code == "login_busy":
        return _("Sky is signing in right now. Run matching again in a minute.")
    if isinstance(exc, (AuthRequired, TransientError, PermanentError)):
        if (
            isinstance(exc, AuthRequired)
            or provider_operations.classify_exception("reconcile", exc).error_code
            == "login_failed"
        ):
            return _(
                "Could not sign in to Sky. Check the connection settings on the "
                "company page."
            )
        if isinstance(exc, TransientError):
            return _(
                "Could not reach Sky. Check the internet connection and try again."
            )
        return _("Sky refused the request: %(error)s") % {"error": code}
    log.exception("Sky report matching failed for company %s", company.pk)
    return _(
        "Matching failed unexpectedly. The details were saved in the server log."
    )


@management_required
def sky_reconcile(request, pk):
    company = get_object_or_404(Company, pk=pk)
    blocked = _reconcile_blocked_message(
        company, "sky", _("Sky report matching is only available for Sky.")
    )
    if blocked:
        messages.error(request, blocked)
        return redirect("companies:company_detail", pk=pk)

    form = SkyReportReconcileForm(request.POST or None)
    result = None
    template = "companies/sky_reconcile.html"
    if request.method == "POST" and form.is_valid():
        period_from = form.cleaned_data["period_from"]
        period_to = form.cleaned_data["period_to"]
        min_diff = form.cleaned_data.get("min_amount_diff")
        if min_diff is None:
            min_diff = 3
        try:
            result, template = provider_service.reconcile(
                company,
                period_from,
                period_to,
                min_amount_diff=min_diff,
                user_id=request.user.pk,
            )
            messages.success(
                request,
                _("Sky report processed (%(rows)s rows).") % {"rows": result.row_count},
            )
        except Exception as exc:
            if isinstance(exc, PermanentError) and str(exc) == RECONCILE_NOT_SUPPORTED:
                messages.error(request, _("Sky report matching is only available for Sky."))
                return redirect("companies:company_detail", pk=pk)
            messages.error(request, _sky_error_message(company, exc))

    return render(
        request,
        template,
        {
            "company": company,
            "form": form,
            "result": result,
            "provider": "sky",
            "supplier_label": _("Sky"),
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
    q = (request.GET.get("q") or "").strip()
    company_id = (request.GET.get("company") or "").strip()
    show_archived = (request.GET.get("show_archived") or "").strip() in {
        "1",
        "true",
        "on",
        "yes",
    }

    variants_qs = Product.objects.annotate(
        has_sales_annotated=_has_sales_subquery("product_id"),
    ).order_by("variant_label", "pk")
    if not show_archived:
        variants_qs = variants_qs.filter(is_active=True)

    lines = (
        ProductLine.objects.select_related("company", "default_package")
        .annotate(has_sales_annotated=_has_sales_subquery("product__line_id"))
        .prefetch_related(models.Prefetch("variants", queryset=variants_qs))
        .order_by("company__name", "sort_order", "name")
    )
    if company_id.isdigit():
        lines = lines.filter(company_id=int(company_id))
    if q:
        package_match = Q(variants__variant_label__icontains=q)
        if not show_archived:
            package_match &= Q(variants__is_active=True)
        lines = lines.filter(
            Q(name__icontains=q) | Q(company__name__icontains=q) | package_match
        ).distinct()
    if not show_archived:
        # Hide lines whose packages are all archived; keep empty lines so new
        # packages can still be added.
        lines = lines.annotate(
            total_packages=Count("variants", distinct=True),
            active_packages=Count(
                "variants",
                filter=Q(variants__is_active=True),
                distinct=True,
            ),
        ).filter(Q(active_packages__gt=0) | Q(total_packages=0))

    companies = Company.objects.order_by("name")
    lines_list = list(lines)
    company_groups = []
    for _company_id, group_iter in groupby(lines_list, key=lambda line: line.company_id):
        group_lines = list(group_iter)
        company_groups.append(
            {
                "company": group_lines[0].company,
                "lines": group_lines,
            }
        )
    return render(
        request,
        "companies/product_list.html",
        {
            "lines": lines_list,
            "company_groups": company_groups,
            "companies": companies,
            "q": q,
            "company_id": company_id,
            "show_archived": show_archived,
            "expand_packages": bool(q),
            "title": _("Products"),
            "card_form": ProductSearchForm(initial=request.GET.dict(), companies=companies),
        },
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
            _(
                "Cannot delete this package because it has sales recorded. "
                "Archive it instead to hide it from new sales."
            ),
        )
        return redirect("companies:product_list")
    label = variant.display_name
    variant.delete()
    messages.success(request, _("Package was deleted: %(label)s") % {"label": label})
    return redirect("companies:product_list")


def _clear_default_package_if_needed(variant: Product) -> None:
    ProductLine.objects.filter(default_package_id=variant.pk).update(default_package=None)


@management_required
@transaction.atomic
def product_variant_archive(request, pk):
    """Hide a package from pickers/lists without deleting historical sales."""
    variant = get_object_or_404(Product.objects.select_related("line"), pk=pk)
    if request.method != "POST":
        return redirect("companies:product_list")
    if not variant.is_active:
        messages.info(request, _("This package is already archived."))
        return redirect("companies:product_list")
    _clear_default_package_if_needed(variant)
    variant.is_active = False
    variant.save(update_fields=["is_active", "updated_at"])
    messages.success(
        request,
        _(
            "Package archived: %(label)s. It is hidden from new sales; "
            "past sales are unchanged."
        )
        % {"label": variant.display_name},
    )
    return redirect("companies:product_list")


@management_required
@transaction.atomic
def product_variant_unarchive(request, pk):
    variant = get_object_or_404(Product.objects.select_related("line"), pk=pk)
    if request.method != "POST":
        return redirect("companies:product_list")
    if variant.is_active:
        messages.info(request, _("This package is already active."))
        return redirect("companies:product_list")
    variant.is_active = True
    variant.save(update_fields=["is_active", "updated_at"])
    messages.success(
        request,
        _("Package restored: %(label)s") % {"label": variant.display_name},
    )
    return redirect("companies:product_list")
