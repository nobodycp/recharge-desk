"""Company page "الربط مع المزوّد" tab: context builder and POST handlers.

The provider connection (credentials, login test, per-service switches) is keyed
by the company's ``provider_key`` and shared by every company on that system.
Secrets are write-only: stored values are never rendered back.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from companies.forms_connection import (
    SERVICE_LABELS,
    CompanySystemForm,
    ConnectionServicesForm,
)
from companies.models import Company
from providers import health, login_test
from providers.adapters.areen.client import OTP_REJECTED, login_needs_otp
from providers.contracts import Capability
from providers.forms import ProviderAccountForm
from providers.models import ProviderConnection
from providers.registry import ADAPTERS
from providers.service import service_state

TAB_CONNECTION = "connection"

RECONCILE_URLS = {
    "layan": "companies:layan_reconcile",
    "sky": "companies:sky_reconcile",
}


def redirect_to_tab(company):
    url = reverse("companies:company_detail", kwargs={"pk": company.pk})
    return redirect(f"{url}?tab={TAB_CONNECTION}")


def company_key(company) -> str:
    """The company's explicit system key when it has a registered adapter, else ``""``."""
    key = (company.provider_key or "").strip().lower()
    return key if key in ADAPTERS else ""


def needs_login(company) -> bool:
    key = company_key(company)
    return bool(key) and ADAPTERS[key].needs_login


LOGIN_TEST_DISABLED_NOTE = _("فعّل الربط أولاً لاختبار تسجيل الدخول")


def login_status_context(company) -> dict:
    key = company_key(company)
    connection = ProviderConnection.objects.filter(provider_key=key).first()
    login = login_test.status(key)
    login["otp_required"] = (
        not login["pending"]
        and login["last_login_ok"] is False
        and login_needs_otp(login["last_error"])
    )
    login["otp_error"] = (
        login["last_error"]
        if login["otp_required"] and OTP_REJECTED in (login["last_error"] or "")
        else ""
    )
    return {
        "company": company,
        "connection_key": key,
        "login": login,
        # No row yet means the model defaults apply (master switch on).
        "login_enabled": connection is None or connection.is_enabled,
        "login_disabled_note": LOGIN_TEST_DISABLED_NOTE,
    }


def _service_rows(company, form) -> list[dict]:
    key = company_key(company)
    rows = []
    for capability, field_name in ProviderConnection.SERVICE_FIELDS.items():
        state = service_state(company, capability)
        reconcile_url = ""
        if (
            capability is Capability.RECONCILE
            and state.code == "active"
            and key in RECONCILE_URLS
        ):
            reconcile_url = reverse(RECONCILE_URLS[key], kwargs={"pk": company.pk})
        rows.append(
            {
                "label": SERVICE_LABELS[capability],
                "field": form[field_name],
                "state": state,
                "reconcile_url": reconcile_url,
            }
        )
    return rows


def build_connection_context(company) -> dict:
    key = company_key(company)
    ctx = {
        "company": company,
        "connection_key": key,
        "system_form": CompanySystemForm(instance=company),
        "connection_health": health.status(),
        "shared_with": [],
    }
    if not key:
        return ctx

    # A row must exist before the login test is offered (status() stays
    # "pending" until expiry without one) and for the services form.
    connection = ProviderConnection.for_key(key)
    services_form = ConnectionServicesForm(instance=connection)
    ctx.update(
        services_form=services_form,
        service_rows=_service_rows(company, services_form),
        shared_with=list(
            Company.objects.filter(provider_key=key)
            .exclude(pk=company.pk)
            .order_by("name")
            .values_list("name", flat=True)
        ),
    )
    if ADAPTERS[key].needs_login:
        ctx.update(
            account_form=ProviderAccountForm(provider_key=key),
            **login_status_context(company),
        )
    return ctx


def _flash_form_errors(request, form) -> None:
    for errors in form.errors.values():
        for error in errors:
            messages.error(request, error)


def save_system(request, company):
    form = CompanySystemForm(request.POST, instance=company)
    if form.is_valid():
        company.provider_key = form.cleaned_data["provider_key"]
        company.save(update_fields=["provider_key"])
        messages.success(request, _("تم حفظ نظام الشركة."))
    else:
        _flash_form_errors(request, form)
    return redirect_to_tab(company)


def save_services(request, company):
    key = company_key(company)
    if not key:
        raise Http404("No provider system")
    form = ConnectionServicesForm(request.POST, instance=ProviderConnection.for_key(key))
    if form.is_valid():
        form.save()
        messages.success(request, _("تم حفظ إعدادات الخدمات."))
    else:
        _flash_form_errors(request, form)
    return redirect_to_tab(company)


def save_account(request, company):
    if not needs_login(company):
        raise Http404("This system has no login account")
    form = ProviderAccountForm(request.POST, provider_key=company_key(company))
    if form.is_valid():
        form.save()
        messages.success(request, _("تم حفظ حساب المزوّد."))
    else:
        _flash_form_errors(request, form)
    return redirect_to_tab(company)


_TEST_MESSAGES = {
    "started": (
        messages.info,
        _("بدأ اختبار تسجيل الدخول — حدّث الصفحة بعد لحظات لرؤية النتيجة."),
    ),
    "pending": (messages.info, _("اختبار تسجيل الدخول قيد التنفيذ بالفعل.")),
    "unavailable": (
        messages.error,
        _("تعذّر بدء الاختبار — تحقق من عمل Redis وعامل المهام."),
    ),
}


def start_login_test(request, company):
    """Enqueue the login test on the worker (never a login inside the request)."""
    if not needs_login(company):
        raise Http404("This system has no login account")
    key = company_key(company)
    if not ProviderConnection.for_key(key).is_enabled:
        # Master switch off: the services would refuse a login anyway.
        if request.headers.get("HX-Request"):
            ctx = login_status_context(company)
            ctx["disabled"] = True
            return render(
                request, "companies/partials/connection_login_status.html", ctx
            )
        messages.error(request, LOGIN_TEST_DISABLED_NOTE)
        return redirect_to_tab(company)
    otp_code = (request.POST.get("otp_code") or "").strip()
    result = login_test.start(key, otp_code) if otp_code else login_test.start(key)
    if request.headers.get("HX-Request"):
        ctx = login_status_context(company)
        ctx["unavailable"] = result == "unavailable"
        return render(request, "companies/partials/connection_login_status.html", ctx)
    flash, text = _TEST_MESSAGES[result]
    flash(request, text)
    return redirect_to_tab(company)


def login_status(request, company):
    """HTMX poll target for the login test result."""
    if not needs_login(company):
        raise Http404("This system has no login account")
    ProviderConnection.for_key(company_key(company))
    ctx = login_status_context(company)
    response = render(
        request,
        "companies/partials/connection_login_status.html",
        ctx,
    )
    # The poll is the moment we learn the provider asked for the SMS code.
    if request.headers.get("HX-Request") and ctx["login"]["otp_required"]:
        response["HX-Trigger"] = json.dumps(
            {"rdToast": {"message": ctx["login"]["last_error"], "level": "warning"}}
        )
    return response
