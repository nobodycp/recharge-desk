"""Management queue for employee-submitted customer payments."""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from accounts.permissions import management_required
from core.pagination import paginate_request
from customers.models import CustomerPaymentSubmission
from customers.services import (
    approve_customer_payment_submission,
    reject_customer_payment_submission,
)
from customers.views._shared import flash_form_errors


class RejectSubmissionForm(forms.Form):
    reason = forms.CharField(
        label=_("Reason"),
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 2}),
    )


@management_required
def customer_payment_submissions_list(request):
    qs = (
        CustomerPaymentSubmission.objects.filter(status=CustomerPaymentSubmission.Status.AWAITING)
        .select_related("customer", "payment_method", "created_by")
        .order_by("created_at", "id")
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(customer__name__icontains=q)
            | Q(created_by__username__icontains=q)
            | Q(payment_method__name__icontains=q)
        )
    page_obj = paginate_request(request, qs)
    return render(
        request,
        "customers/payment_submissions_list.html",
        {
            "title": _("Customer payments awaiting approval"),
            "page_obj": page_obj,
            "q": q,
        },
    )


@management_required
def customer_payment_submission_approve(request, pk):
    if request.method != "POST":
        return redirect("customers:customer_payment_submissions_list")
    sub = get_object_or_404(
        CustomerPaymentSubmission,
        pk=pk,
        status=CustomerPaymentSubmission.Status.AWAITING,
    )
    try:
        approve_customer_payment_submission(submission=sub, user=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Payment approved and applied to the customer account."))
    return redirect("customers:customer_payment_submissions_list")


@management_required
def customer_payment_submission_reject(request, pk):
    if request.method != "POST":
        return redirect("customers:customer_payment_submissions_list")
    sub = get_object_or_404(
        CustomerPaymentSubmission,
        pk=pk,
        status=CustomerPaymentSubmission.Status.AWAITING,
    )
    form = RejectSubmissionForm(request.POST)
    if not form.is_valid():
        flash_form_errors(request, form)
        return redirect("customers:customer_payment_submissions_list")
    try:
        reject_customer_payment_submission(
            submission=sub,
            user=request.user,
            reason=form.cleaned_data.get("reason") or "",
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Submission rejected."))
    return redirect("customers:customer_payment_submissions_list")
