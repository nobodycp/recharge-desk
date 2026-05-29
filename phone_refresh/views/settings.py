"""Management UI: provider-rule CRUD, customer-message CRUD, status CRUD, logs."""
from __future__ import annotations

import hashlib
import secrets
import time

from django.contrib import messages
from django.db.models import Count, ProtectedError, Q
from django.http import Http404, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import management_required
from core.pagination import paginate_request
from phone_refresh.forms import (
    CustomerMessageForm,
    InternalTestForm,
    ProviderResponseRuleForm,
    ProviderTestForm,
    PublicPageTokenAssignForm,
    RefreshStatusForm,
    SiteSettingsForm,
    SystemSettingsForm,
)
from phone_refresh.models import (
    ApiSettings,
    ApiToken,
    CustomerMessage,
    PhoneProvider,
    ProviderConfig,
    ProviderResponseRule,
    RefreshLog,
    RefreshSource,
    RefreshStatus,
    SiteSettings,
    SystemSettings,
)
from phone_refresh.services.refresh_service import refresh_phone
from phone_refresh.views.general import GENERAL_TAB_ID

PROVIDER_TABS = [(p.value, p.label) for p in PhoneProvider]
STATUSES_TAB_ID = "statuses"
MESSAGES_TAB_ID = "messages"
SITE_MANAGEMENT_TAB_ID = "site_management"
PROVIDERS_GENERAL_TAB_ID = "general"


def _ensure_provider(provider: str) -> str:
    """Validate ``provider`` is a real ``PhoneProvider`` choice."""
    if provider not in {p.value for p in PhoneProvider}:
        raise Http404("Unknown provider")
    return provider


def _settings_url(active_tab: str) -> str:
    return f"{reverse('phone_refresh:settings')}?tab={active_tab}"


def _providers_url(active_tab: str) -> str:
    return f"{reverse('phone_refresh:providers_index')}?tab={active_tab}"


def _available_statuses_for_create():
    used_ids = CustomerMessage.objects.values_list("status_id", flat=True)
    return RefreshStatus.objects.exclude(pk__in=used_ids)


def _create_public_page_token(*, created_by=None) -> tuple[ApiToken, str]:
    """Create a fresh ``ApiToken`` row and return ``(token, raw_value)``."""
    raw_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    token = ApiToken.objects.create(
        name="صفحة التحديث العامة",
        token_hash=token_hash,
        prefix=raw_token[:8],
        created_by=created_by if created_by and created_by.is_authenticated else None,
    )
    return token, raw_token


def _assign_public_page_token(site_settings: SiteSettings, token: ApiToken, raw: str) -> None:
    site_settings.public_page_token = token
    site_settings.public_page_token_raw = raw
    site_settings.save(update_fields=[
        "public_page_token",
        "public_page_token_raw",
        "updated_at",
    ])


def _clear_public_page_token(site_settings: SiteSettings) -> None:
    site_settings.public_page_token = None
    site_settings.public_page_token_raw = ""
    site_settings.save(update_fields=[
        "public_page_token",
        "public_page_token_raw",
        "updated_at",
    ])


@management_required
def settings_index(request):
    """Tabbed settings page: General + statuses + customer messages + site mgmt."""
    valid_tabs = {GENERAL_TAB_ID, STATUSES_TAB_ID, MESSAGES_TAB_ID, SITE_MANAGEMENT_TAB_ID}
    active_tab = request.GET.get("tab") or GENERAL_TAB_ID
    if active_tab not in valid_tabs:
        active_tab = GENERAL_TAB_ID

    customer_messages = list(
        CustomerMessage.objects.select_related("status")
        .order_by("status__sort_order", "status__id")
    )
    statuses = list(RefreshStatus.objects.all())
    can_add_message = len(customer_messages) < len(statuses)

    system_settings = SystemSettings.get()
    settings_form = SystemSettingsForm(instance=system_settings)
    internal_test_form = InternalTestForm()

    # ── إدارة الموقع form: handle POST on the same view so the tab is
    # fully self-contained (no separate URL/view to wire up).
    site_settings = SiteSettings.get_solo()
    api_settings = ApiSettings.get()
    site_settings_form = SiteSettingsForm(instance=site_settings)
    public_page_token_form = PublicPageTokenAssignForm(site_settings=site_settings)

    if request.method == "POST" and active_tab == SITE_MANAGEMENT_TAB_ID:
        form_type = request.POST.get("form")

        if form_type == "site_settings":
            site_settings_form = SiteSettingsForm(request.POST, instance=site_settings)
            if site_settings_form.is_valid():
                site_settings_form.save()
                messages.success(request, "تم حفظ إعدادات الموقع.")
                return redirect(_settings_url(SITE_MANAGEMENT_TAB_ID))
        elif form_type == "generate_public_page_token":
            token, raw_token = _create_public_page_token(created_by=request.user)
            _assign_public_page_token(site_settings, token, raw_token)
            request.session["show_public_page_token"] = raw_token
            messages.success(request, "تم إنشاء توكن جديد للصفحة العامة.")
            return redirect(_settings_url(SITE_MANAGEMENT_TAB_ID))
        elif form_type == "assign_public_page_token":
            public_page_token_form = PublicPageTokenAssignForm(
                request.POST,
                site_settings=site_settings,
            )
            if public_page_token_form.is_valid():
                token = public_page_token_form.cleaned_data["public_page_token"]
                raw = public_page_token_form.cleaned_data["public_page_token_raw"]
                _assign_public_page_token(site_settings, token, raw)
                messages.success(request, "تم تعيين توكن الصفحة العامة.")
                return redirect(_settings_url(SITE_MANAGEMENT_TAB_ID))
        elif form_type == "clear_public_page_token":
            _clear_public_page_token(site_settings)
            messages.success(request, "تم إزالة توكن الصفحة العامة.")
            return redirect(_settings_url(SITE_MANAGEMENT_TAB_ID))

    show_generated_token = request.session.pop("show_public_page_token", "")
    site_settings.refresh_from_db()

    ctx = {
        "title": "إعدادات تحديث الأرقام",
        "active_tab": active_tab,
        "general_tab_id": GENERAL_TAB_ID,
        "statuses_tab_id": STATUSES_TAB_ID,
        "messages_tab_id": MESSAGES_TAB_ID,
        "site_management_tab_id": SITE_MANAGEMENT_TAB_ID,
        "customer_messages": customer_messages,
        "refresh_statuses": statuses,
        "can_add_message": can_add_message,
        "system_settings": system_settings,
        "settings_form": settings_form,
        "internal_test_form": internal_test_form,
        "site_settings": site_settings,
        "site_settings_form": site_settings_form,
        "public_page_token_form": public_page_token_form,
        "api_settings": api_settings,
        "show_generated_token": show_generated_token,
        "current_host": request.get_host(),
    }
    return render(request, "phone_refresh/settings_index.html", ctx)


def _provider_configs_ordered() -> list[ProviderConfig]:
    """Return one ``ProviderConfig`` per ``PhoneProvider``, in tab order.

    Missing rows are materialized on the fly (default ``is_enabled=True``)
    so the General tab always renders the full set of 4 toggles even if
    a row was deleted from the admin.
    """
    rows = {cfg.provider: cfg for cfg in ProviderConfig.objects.all()}
    ordered: list[ProviderConfig] = []
    for value, _label in PROVIDER_TABS:
        cfg = rows.get(value)
        if cfg is None:
            cfg = ProviderConfig.objects.create(provider=value, is_enabled=True)
        ordered.append(cfg)
    return ordered


@management_required
def providers_index(request):
    """Tabbed providers page: General (toggles + test) + one tab per provider."""
    provider_keys = {p.value for p in PhoneProvider}
    valid_tabs = provider_keys | {PROVIDERS_GENERAL_TAB_ID}
    default_tab = PROVIDERS_GENERAL_TAB_ID
    active_tab = request.GET.get("tab") or default_tab
    if active_tab not in valid_tabs:
        active_tab = default_tab

    current_rules: list[ProviderResponseRule] = []
    if active_tab in provider_keys:
        current_rules = list(
            ProviderResponseRule.objects.filter(provider=active_tab)
            .select_related("target_status")
            .order_by("order", "id")
        )

    provider_configs = _provider_configs_ordered()
    provider_test_form = ProviderTestForm()

    ctx = {
        "title": "مزوّدو تحديث الأرقام",
        "active_tab": active_tab,
        "general_tab_id": PROVIDERS_GENERAL_TAB_ID,
        "provider_tabs": PROVIDER_TABS,
        "current_rules": current_rules,
        "match_types": ProviderResponseRule._meta.get_field("match_type").choices,
        "provider_configs": provider_configs,
        "provider_labels": dict(PROVIDER_TABS),
        "provider_test_form": provider_test_form,
    }
    return render(request, "phone_refresh/providers_index.html", ctx)


@management_required
@require_POST
def providers_general_save(request):
    """POST-only: persist the on/off toggles for all 4 providers in one go."""
    rows = _provider_configs_ordered()
    for cfg in rows:
        field = f"is_enabled_{cfg.provider}"
        new_value = field in request.POST
        if cfg.is_enabled != new_value:
            cfg.is_enabled = new_value
            cfg.save(update_fields=["is_enabled", "updated_at"])
    messages.success(request, "تم حفظ إعدادات المزوّدين.")
    return redirect(f"{reverse('phone_refresh:providers_index')}?tab={PROVIDERS_GENERAL_TAB_ID}")


@management_required
@require_POST
def providers_test(request):
    """Admin-only AJAX: call one specific provider directly and return the full trace."""
    form = ProviderTestForm(request.POST)
    if not form.is_valid():
        first_error = next(iter(form.errors.values()), ["خطأ في المدخلات."])[0]
        return JsonResponse({"ok": False, "error": first_error}, status=400)

    phone = form.cleaned_data["phone"]
    provider_key = form.cleaned_data["provider"]

    started = time.monotonic()
    result = refresh_phone(
        phone,
        ip=None,
        internal_test=True,
        forced_provider=provider_key,
        bypass_provider_off=True,
        source=RefreshSource.INTERNAL_TEST,
    )
    total_ms = int((time.monotonic() - started) * 1000)

    matched_rule_payload = None
    if result.matched_rule_id:
        rule = (
            ProviderResponseRule.objects.filter(pk=result.matched_rule_id)
            .select_related("target_status")
            .first()
        )
        if rule is not None:
            matched_rule_payload = {
                "id": rule.pk,
                "match_type": rule.get_match_type_display(),
                "pattern": rule.pattern,
                "expected_value": rule.expected_value,
                "order": rule.order,
                "target_status": rule.target_status.label,
            }

    return JsonResponse(
        {
            "ok": True,
            "phone": phone,
            "provider": result.provider,
            "provider_label": dict(PROVIDER_TABS).get(result.provider or "", ""),
            "status": result.status.code,
            "status_label": result.status.label,
            "matched_rule": matched_rule_payload,
            "message": {
                "title": result.message_title,
                "body": result.message_body,
            },
            "raw_status_code": result.raw_status_code,
            # Full upstream body (no truncation) so reviewers can diagnose
            # the real response. The DB-persisted RefreshLog.raw_excerpt
            # stays capped at 500 chars; this field is in-memory only.
            "raw_body": result.raw_body_full,
            "raw_body_length": len(result.raw_body_full),
            "html_form_detected": result.html_form_detected,
            # Kept for backwards compatibility with anything still reading it.
            "raw_excerpt": result.raw_body_full,
            "error": result.error,
            "duration_ms": total_ms,
        }
    )


# ---------------------------------------------------------------------------
# RefreshStatus CRUD
# ---------------------------------------------------------------------------


@management_required
def status_create(request):
    form = RefreshStatusForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم إضافة الحالة.")
        return redirect(_settings_url(STATUSES_TAB_ID))
    return render(
        request,
        "phone_refresh/status_form.html",
        {"form": form, "title": "حالة جديدة"},
    )


@management_required
def status_edit(request, pk: int):
    status = get_object_or_404(RefreshStatus, pk=pk)
    form = RefreshStatusForm(request.POST or None, instance=status)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم تحديث الحالة.")
        return redirect(_settings_url(STATUSES_TAB_ID))
    return render(
        request,
        "phone_refresh/status_form.html",
        {
            "form": form,
            "status": status,
            "title": f"تعديل الحالة — {status.label}",
        },
    )


@management_required
def status_delete(request, pk: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    status = get_object_or_404(RefreshStatus, pk=pk)
    if status.is_system:
        messages.error(request, "حالة نظام لا يمكن حذفها.")
        return redirect(_settings_url(STATUSES_TAB_ID))
    try:
        status.delete()
    except ProtectedError:
        messages.error(
            request,
            "لا يمكن حذف هذه الحالة لأنها مستخدمة في رسائل أو قواعد أو سجلات تحديث.",
        )
        return redirect(_settings_url(STATUSES_TAB_ID))
    messages.success(request, "تم حذف الحالة.")
    return redirect(_settings_url(STATUSES_TAB_ID))


# ---------------------------------------------------------------------------
# CustomerMessage CRUD
# ---------------------------------------------------------------------------


@management_required
def message_create(request):
    """Create a new ``CustomerMessage`` for one of the unused statuses."""
    available = _available_statuses_for_create()
    if not available.exists():
        messages.error(request, "تم إنشاء رسالة لكل الحالات.")
        return redirect(_settings_url(MESSAGES_TAB_ID))

    form = CustomerMessageForm(request.POST or None, available_statuses=available)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم إضافة الرسالة.")
        return redirect(_settings_url(MESSAGES_TAB_ID))
    return render(
        request,
        "phone_refresh/message_form.html",
        {
            "form": form,
            "title": "رسالة جديدة",
        },
    )


@management_required
def message_edit(request, pk: int):
    """Edit an existing ``CustomerMessage`` (status is read-only)."""
    message = get_object_or_404(CustomerMessage.objects.select_related("status"), pk=pk)
    form = CustomerMessageForm(request.POST or None, instance=message)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم تحديث الرسالة.")
        return redirect(_settings_url(MESSAGES_TAB_ID))
    return render(
        request,
        "phone_refresh/message_form.html",
        {
            "form": form,
            "message": message,
            "title": f"تعديل الرسالة — {message.status.label}",
        },
    )


@management_required
def message_delete(request, pk: int):
    """POST-only: delete a ``CustomerMessage`` row."""
    message = get_object_or_404(CustomerMessage, pk=pk)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    message.delete()
    messages.success(request, "تم حذف الرسالة.")
    return redirect(_settings_url(MESSAGES_TAB_ID))


# ---------------------------------------------------------------------------
# ProviderResponseRule CRUD
# ---------------------------------------------------------------------------


@management_required
def rule_create(request, provider: str):
    provider = _ensure_provider(provider)
    form = ProviderResponseRuleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        rule = form.save(commit=False)
        rule.provider = provider
        rule.save()
        messages.success(request, "تم إضافة القاعدة.")
        return redirect(_providers_url(provider))
    return render(
        request,
        "phone_refresh/rule_form.html",
        {
            "form": form,
            "provider": provider,
            "title": f"قاعدة جديدة — {provider}",
        },
    )


@management_required
def rule_edit(request, pk: int):
    rule = get_object_or_404(ProviderResponseRule, pk=pk)
    form = ProviderResponseRuleForm(request.POST or None, instance=rule)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم تحديث القاعدة.")
        return redirect(_providers_url(rule.provider))
    return render(
        request,
        "phone_refresh/rule_form.html",
        {
            "form": form,
            "provider": rule.provider,
            "rule": rule,
            "title": f"تعديل القاعدة — {rule.provider}",
        },
    )


@management_required
def rule_delete(request, pk: int):
    rule = get_object_or_404(ProviderResponseRule, pk=pk)
    if request.method != "POST":
        return redirect(_providers_url(rule.provider))
    provider = rule.provider
    rule.delete()
    messages.success(request, "تم حذف القاعدة.")
    return redirect(_providers_url(provider))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


SOURCE_FILTER_CHOICES: list[tuple[str, str]] = [
    (RefreshSource.WEB.value, "الويب"),
    (RefreshSource.API.value, "API"),
    (RefreshSource.EMPLOYEE.value, "المبيعات"),
    (RefreshSource.INTERNAL_TEST.value, "اختبار داخلي"),
    (RefreshSource.LEGACY.value, "قديم"),
]
_VALID_SOURCE_VALUES = {value for value, _ in SOURCE_FILTER_CHOICES}


def _apply_report_filters(request_params):
    """Shared filter parser for the reports list view and bulk-delete.

    Returns the filtered queryset plus a dict of the normalized
    (validated) parameter values, so the two callers stay in lock-step.
    """
    qs = RefreshLog.objects.select_related("matched_rule", "status").order_by("-created_at")

    provider = (request_params.get("provider") or "").strip()
    if provider in {p.value for p in PhoneProvider}:
        qs = qs.filter(provider=provider)
    else:
        provider = ""

    status_codes = set(RefreshStatus.objects.values_list("code", flat=True))
    status_code = (request_params.get("status") or "").strip()
    if status_code in status_codes:
        qs = qs.filter(status__code=status_code)
    else:
        status_code = ""

    source = (request_params.get("source") or "").strip()
    if source in _VALID_SOURCE_VALUES:
        qs = qs.filter(source=source)
    else:
        source = ""

    q = (request_params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(phone__icontains=q) | Q(ip__icontains=q))

    return qs, {
        "provider": provider,
        "status": status_code,
        "source": source,
        "q": q,
    }


@management_required
def reports_list(request):
    """Paginated log of refresh attempts with simple filters."""
    qs, applied = _apply_report_filters(request.GET)

    summary = list(
        RefreshLog.objects.values("status__code", "status__label")
        .annotate(n=Count("id"))
        .order_by("status__sort_order", "status__id")
    )

    page_obj = paginate_request(request, qs, per_page=50)

    ctx = {
        "title": "تقارير تحديث الأرقام",
        "page_obj": page_obj,
        "providers": PROVIDER_TABS,
        "statuses": list(RefreshStatus.objects.all()),
        "source_choices": SOURCE_FILTER_CHOICES,
        "selected_provider": applied["provider"],
        "selected_status": applied["status"],
        "selected_source": applied["source"],
        "q": applied["q"],
        "summary": summary,
    }
    return render(request, "phone_refresh/reports.html", ctx)


@management_required
def report_log_detail(request, pk: int):
    """Return one ``RefreshLog`` row as JSON for the reports detail modal.

    Includes the full upstream ``raw_body`` (capped at
    ``MAX_RAW_BODY_CHARS`` at write time). ``html_form_detected`` and
    ``user_agent`` are not persisted on the model today; they're emitted
    as ``null`` so the frontend contract is stable if/when we add them.
    """
    log_row = get_object_or_404(
        RefreshLog.objects.select_related("status", "matched_rule"),
        pk=pk,
    )

    provider_label = ""
    for value, label in PROVIDER_TABS:
        if value == log_row.provider:
            provider_label = label
            break

    matched_rule_payload = None
    if log_row.matched_rule is not None:
        rule = log_row.matched_rule
        matched_rule_payload = {
            "id": rule.pk,
            "match_type": rule.match_type,
            "pattern": rule.pattern,
            "expected_value": rule.expected_value,
            "order": rule.order,
            "note": rule.note,
        }

    source_label = dict(SOURCE_FILTER_CHOICES).get(log_row.source, log_row.source or "")

    payload = {
        "id": log_row.pk,
        "phone": log_row.phone,
        "provider": log_row.provider or "",
        "provider_label": provider_label,
        "status_code": log_row.status.code,
        "status_label": log_row.status.label,
        "source": log_row.source or "",
        "source_label": source_label,
        "matched_rule": matched_rule_payload,
        "http_status": log_row.raw_status_code,
        "duration_ms": log_row.duration_ms,
        "ip": log_row.ip or "",
        "created_at": log_row.created_at.isoformat(),
        "raw_excerpt": log_row.raw_excerpt or "",
        "raw_body": log_row.raw_body or "",
        # Not persisted today — emitted for forward-compat with the UI.
        "error": None,
        "html_form_detected": None,
        "user_agent": None,
    }
    return JsonResponse(payload)


# ---------------------------------------------------------------------------
# Bulk delete
# ---------------------------------------------------------------------------


def _is_ajax(request) -> bool:
    return (
        request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
        or "application/json" in (request.headers.get("accept", "").lower())
    )


@management_required
@require_POST
def report_logs_bulk_delete(request):
    """Delete a batch of ``RefreshLog`` rows.

    Two modes:

    * ``mode=ids``    + ``ids=1,2,3`` → delete those specific rows.
    * ``mode=filter`` → re-apply the same filter parsing as
      :func:`reports_list` (provider / status / source / q) to the
      provided POST params and delete every matching row.

    Returns a JSON ``{deleted: N}`` envelope for AJAX callers; falls back
    to a Django messages flash + redirect for plain form posts.
    """
    mode = (request.POST.get("mode") or "").strip().lower()

    if mode == "ids":
        raw_ids = request.POST.get("ids") or ""
        ids: list[int] = []
        for chunk in raw_ids.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                ids.append(int(chunk))
            except ValueError:
                continue
        if not ids:
            deleted = 0
        else:
            deleted, _ = RefreshLog.objects.filter(pk__in=ids).delete()
    elif mode == "filter":
        qs, _applied = _apply_report_filters(request.POST)
        # ``RefreshLog.objects.delete()`` would also try to cascade, but
        # this model has no children pointing at it with CASCADE — the
        # only inbound FKs are ``matched_rule`` (PROTECT on the rule
        # side) and ``status`` (PROTECT on the status side), neither of
        # which blocks deleting the log row itself.
        deleted, _ = qs.delete()
    else:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "invalid_mode"}, status=400)
        messages.error(request, "وضع حذف غير معروف.")
        return redirect(reverse("phone_refresh:reports"))

    if _is_ajax(request):
        return JsonResponse({"ok": True, "deleted": int(deleted)})

    messages.success(request, f"تم حذف {int(deleted)} سجل.")
    return redirect(reverse("phone_refresh:reports"))
