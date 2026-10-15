"""CRUD views for the PaymentMethod taxonomy (management-only)."""

from django.apps import apps
from django.contrib import messages
from django.db import transaction
from django.db.models import Exists, OuterRef, ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from accounts.permissions import management_required
from core.pagination import paginate_request
from sales.forms import PaymentMethodForm
from sales.models import PaymentMethod


def _has_refs_subquery():
    """`OR` of three `Exists()` subqueries — folds into one SELECT so the
    list page does not fire a fresh EXISTS per row (see
    `PaymentMethod.is_deletable`)."""
    Sale = apps.get_model("sales", "Sale")
    CustomerPayment = apps.get_model("customers", "CustomerPayment")
    CustomerPaymentSubmission = apps.get_model("customers", "CustomerPaymentSubmission")
    return (
        Exists(Sale.objects.filter(payment_method_id=OuterRef("pk")))
        | Exists(CustomerPayment.objects.filter(payment_method_id=OuterRef("pk")))
        | Exists(CustomerPaymentSubmission.objects.filter(payment_method_id=OuterRef("pk")))
    )


@management_required
def payment_method_list(request):
    qs = PaymentMethod.objects.annotate(has_refs_annotated=_has_refs_subquery()).order_by("name")
    page_obj = paginate_request(request, qs)
    return render(
        request,
        "sales/payment_method_list.html",
        {"page_obj": page_obj, "title": _("Payment methods")},
    )


@management_required
def payment_method_create(request):
    form = PaymentMethodForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Payment method saved."))
        return redirect("sales:payment_method_list")
    return render(
        request,
        "sales/payment_method_form.html",
        {"form": form, "title": _("New payment method")},
    )


@management_required
def payment_method_edit(request, pk):
    obj = get_object_or_404(PaymentMethod, pk=pk)
    form = PaymentMethodForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Payment method updated."))
        return redirect("sales:payment_method_list")
    return render(
        request,
        "sales/payment_method_form.html",
        {"form": form, "title": _("Edit payment method"), "method": obj},
    )


@management_required
@transaction.atomic
def payment_method_delete(request, pk):
    obj = get_object_or_404(PaymentMethod, pk=pk)
    if request.method != "POST":
        return redirect("sales:payment_method_list")

    name = obj.name
    try:
        obj.delete()
    except ProtectedError:
        # ``Sale`` and ``Payment`` both reference PaymentMethod with
        # on_delete=PROTECT — refuse the delete with a clear message
        # instead of leaking a 500 to the admin.
        messages.error(
            request,
            _(
                "Cannot delete “%(name)s” because existing sales or customer "
                "payments still reference it. Mark it inactive instead, or "
                "first move those records to another method."
            )
            % {"name": name},
        )
        return redirect("sales:payment_method_list")

    messages.success(
        request, _("Payment method “%(name)s” was deleted.") % {"name": name}
    )
    return redirect("sales:payment_method_list")
