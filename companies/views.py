from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from accounts.permissions import management_required
from companies.forms import CompanyForm, ProductLineForm, ProductVariantForm
from companies.models import Company, Product, ProductLine
from companies.query_utils import apply_company_list_ordering
from core.pagination import paginate_request
from sales.services import initialize_company_opening_balance


@management_required
def company_list(request):
    qs = Company.objects.all()
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
    lines = (
        ProductLine.objects.select_related("company", "default_package")
        .prefetch_related("variants")
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
