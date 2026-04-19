"""CRUD views for the PaymentMethod taxonomy (management-only)."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from accounts.permissions import management_required
from core.pagination import paginate_request
from sales.forms import PaymentMethodForm
from sales.models import PaymentMethod


@management_required
def payment_method_list(request):
    qs = PaymentMethod.objects.order_by("name")
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
        {"form": form, "title": _("Edit payment method")},
    )
