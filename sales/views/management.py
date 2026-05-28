"""Management-only sales views: lists, queues, single-sale lifecycle actions."""

from django.contrib import messages
from django.db import DatabaseError, IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from accounts.permissions import management_required
from core.pagination import paginate_request
from customers.services import approve_sale as approve_on_account_sale
from customers.services import reject_sale as reject_on_account_sale
from sales.forms import (
    ManagementSaleEditForm,
    ManagementSaleFilterForm,
)
from sales.models import Sale
from sales.query_utils import (
    apply_management_sale_filter_data,
    apply_sale_list_ordering,
)
from sales.services import (
    cancel_sale,
    delete_sale_permanently,
    mark_sale_paid,
    update_sale_fields,
)
from sales.views._shared import htmx_action_error, htmx_remove_target, is_htmx


# --------------------------------------------------------------- list views
@management_required
def management_sale_list(request):
    qs = Sale.objects.select_related(
        "company",
        "product",
        "product__line",
        "payment_method",
        "created_by",
        "employee_recipient",
        "employee_recipient__user",
        "employee_recipient__user__profile",
    ).order_by("-created_at")
    form = ManagementSaleFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = apply_management_sale_filter_data(qs, data)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(reference_number__icontains=q)
            | Q(payer_name__icontains=q)
            | Q(product__line__name__icontains=q)
            | Q(company__name__icontains=q)
        )
    qs = apply_sale_list_ordering(request, qs)
    page_obj = paginate_request(request, qs)
    ctx = {
        "page_obj": page_obj,
        "filter_form": form,
        "title": _("Sales"),
        "sort": request.GET.get("sort") or "created_at",
        "order": (request.GET.get("order") or "desc").lower(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "sales/partials/management_sale_list_results.html", ctx)
    return render(request, "sales/management_sale_list.html", ctx)


@management_required
def pending_payments(request):
    """Real-money pending sales only — the till expects cash/transfer to land.

    Approved on-account sales also live in PENDING but are settled via the
    customer's account (FIFO on the next CustomerPayment), so they belong
    on the customer detail page, not here.
    """
    qs = (
        Sale.objects.filter(status=Sale.Status.PENDING, on_account=False)
        .select_related(
            "company",
            "product",
            "product__line",
            "payment_method",
            "created_by",
            "employee_recipient",
            "employee_recipient__user",
            "employee_recipient__user__profile",
        )
    )
    form = ManagementSaleFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = apply_management_sale_filter_data(qs, data, omit_status=True)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(reference_number__icontains=q)
            | Q(payer_name__icontains=q)
            | Q(product__line__name__icontains=q)
            | Q(company__name__icontains=q)
        )
    qs = apply_sale_list_ordering(request, qs)
    page_obj = paginate_request(request, qs)
    ctx = {
        "page_obj": page_obj,
        "filter_form": form,
        "title": _("Pending payments"),
        "sort": request.GET.get("sort") or "created_at",
        "order": (request.GET.get("order") or "desc").lower(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "sales/partials/pending_payments_results.html", ctx)
    return render(request, "sales/pending_payments.html", ctx)


@management_required
def awaiting_approvals(request):
    """List on-account sales waiting for management approval."""
    qs = (
        Sale.objects.filter(status=Sale.Status.AWAITING)
        .select_related(
            "company",
            "product",
            "product__line",
            "customer",
            "created_by",
            "employee_recipient",
            "employee_recipient__user",
            "employee_recipient__user__profile",
        )
        .order_by("-created_at")
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(reference_number__icontains=q)
            | Q(payer_name__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(company__name__icontains=q)
        )
    page_obj = paginate_request(request, qs)
    ctx = {
        "page_obj": page_obj,
        "title": _("Awaiting approval"),
        "q": q,
    }
    if request.headers.get("HX-Request"):
        return render(request, "sales/partials/awaiting_results.html", ctx)
    return render(request, "sales/awaiting_approvals.html", ctx)


# ---------------------------------------------------- single-sale lifecycle
def _sale_action_response(
    request,
    *,
    sale_action,
    fallback_redirect: str,
    success_message,
):
    """Boilerplate for the post-only HTMX-aware management actions.

    `sale_action` is a zero-argument callable that performs the side
    effect and may raise ValueError. The helper handles HTMX redirect /
    inline-error semantics and the non-HTMX message + redirect path.
    """
    if request.method != "POST":
        return redirect(fallback_redirect)
    htmx = is_htmx(request)
    try:
        sale_action()
    except ValueError as exc:
        if htmx:
            return htmx_action_error(str(exc))
        messages.error(request, str(exc))
        return redirect(request.META.get("HTTP_REFERER") or fallback_redirect)
    if htmx:
        return htmx_remove_target()
    messages.success(request, success_message)
    return redirect(request.META.get("HTTP_REFERER") or fallback_redirect)


@management_required
def sale_approve(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    return _sale_action_response(
        request,
        sale_action=lambda: approve_on_account_sale(sale=sale, user=request.user),
        fallback_redirect="sales:awaiting_approvals",
        success_message=_("Sale approved and posted to the customer's account."),
    )


@management_required
def sale_reject(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    return _sale_action_response(
        request,
        sale_action=lambda: reject_on_account_sale(sale=sale, user=request.user),
        fallback_redirect="sales:awaiting_approvals",
        success_message=_("Sale rejected and supplier balance restored."),
    )


@management_required
def sale_mark_paid(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    return _sale_action_response(
        request,
        sale_action=lambda: mark_sale_paid(sale=sale, user=request.user),
        fallback_redirect="sales:pending_payments",
        success_message=_("Marked as paid."),
    )


@management_required
def sale_cancel(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    return _sale_action_response(
        request,
        sale_action=lambda: cancel_sale(sale=sale, user=request.user),
        fallback_redirect="sales:management_sale_list",
        success_message=_("Sale cancelled and supplier balance restored."),
    )


@management_required
def sale_edit(request, pk):
    sale = get_object_or_404(
        Sale.objects.select_related("company", "product", "product__line", "payment_method"),
        pk=pk,
    )
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if request.method == "POST":
        form = ManagementSaleEditForm(request.POST, instance=sale)
        if form.is_valid():
            try:
                update_sale_fields(
                    sale=sale,
                    payment_method=form.cleaned_data["payment_method"],
                    payer_name=form.cleaned_data["payer_name"],
                    reference_number=form.cleaned_data["reference_number"],
                    sell_price_actual=form.cleaned_data["sell_price_actual"],
                    notes=form.cleaned_data.get("notes") or "",
                    user=request.user,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Sale updated."))
                return redirect(next_url or "sales:management_sale_list")
    else:
        form = ManagementSaleEditForm(instance=sale)
    return render(
        request,
        "sales/sale_edit.html",
        {
            "form": form,
            "sale": sale,
            "next_url": next_url,
            "title": _("Edit sale"),
        },
    )


@management_required
def sale_delete_permanent(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if request.method != "POST":
        return redirect("sales:management_sale_list")
    htmx = is_htmx(request)
    try:
        delete_sale_permanently(sale=sale, user=request.user)
    except (IntegrityError, DatabaseError) as exc:
        msg = _("Could not delete this sale: %(reason)s") % {"reason": str(exc)}
        if htmx:
            return htmx_action_error(str(msg))
        messages.error(request, msg)
        return redirect(request.META.get("HTTP_REFERER") or "sales:management_sale_list")
    if htmx:
        return htmx_remove_target()
    messages.success(request, _("Sale was permanently removed from the system."))
    return redirect(request.META.get("HTTP_REFERER") or "sales:management_sale_list")
