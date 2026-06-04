"""Management UI under "تحديث الرسائل": settings tabs, devices, replies, reports."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import management_required
from audit.models import AuditAction
from audit.services import record as audit_record
from core.pagination import paginate_request
from phone_refresh.models import RefreshStatus
from sms_gateway.forms import (
    SmsAccessRuleForm,
    SmsApiGatewayForm,
    SmsDeviceForm,
    SmsGeneralSettingsForm,
)
from sms_gateway.models import (
    InboundSms,
    OutboundSms,
    SmsAccessRule,
    SmsGatewayDevice,
    SmsGatewaySettings,
    SmsReplyPolicy,
)
from sms_gateway.services.refresh_client import call_refresh_api
from sms_gateway.services.processing import process_inbound
from sms_gateway.validation import is_valid_number

TAB_GENERAL = "general"
TAB_API = "api"
TAB_DEVICES = "devices"
TAB_REPLIES = "replies"
VALID_TABS = {TAB_GENERAL, TAB_API, TAB_DEVICES, TAB_REPLIES}


def _settings_url(tab: str) -> str:
    return f"{reverse('sms_gateway:settings')}?tab={tab}"


def _ensure_reply_policies():
    existing = set(SmsReplyPolicy.objects.values_list("status_id", flat=True))
    for status in RefreshStatus.objects.all():
        if status.pk not in existing:
            SmsReplyPolicy.objects.create(status=status)


@management_required
def settings_index(request):
    active_tab = request.GET.get("tab") or TAB_GENERAL
    if active_tab not in VALID_TABS:
        active_tab = TAB_GENERAL

    s = SmsGatewaySettings.load()
    ctx = {
        "title": "إعدادات تحديث الرسائل",
        "active_tab": active_tab,
        "general_tab_id": TAB_GENERAL,
        "api_tab_id": TAB_API,
        "devices_tab_id": TAB_DEVICES,
        "replies_tab_id": TAB_REPLIES,
        "settings": s,
        "general_form": SmsGeneralSettingsForm(instance=s),
        "api_form": SmsApiGatewayForm(instance=s),
        "device_form": SmsDeviceForm(),
        "access_rule_form": SmsAccessRuleForm(),
    }
    if active_tab == TAB_DEVICES:
        ctx["devices"] = list(SmsGatewayDevice.objects.all())
        ctx["heartbeat_alerts"] = _heartbeat_alerts(s)
    if active_tab == TAB_REPLIES:
        _ensure_reply_policies()
        ctx["reply_policies"] = list(
            SmsReplyPolicy.objects.select_related("status").all()
        )
        ctx["access_rules"] = list(SmsAccessRule.objects.all())
    return render(request, "sms_gateway/settings_index.html", ctx)


@management_required
@require_POST
def settings_general_save(request):
    s = SmsGatewaySettings.load()
    form = SmsGeneralSettingsForm(request.POST, instance=s)
    if form.is_valid():
        form.save()
        audit_record(AuditAction.UPDATE, s, actor=request.user, extra={"section": "sms_general"})
        messages.success(request, "تم حفظ الإعدادات العامة.")
    else:
        messages.error(request, next(iter(form.errors.values()), ["تعذّر الحفظ."])[0])
    return redirect(_settings_url(TAB_GENERAL))


@management_required
@require_POST
def api_gateway_save(request):
    s = SmsGatewaySettings.load()
    form = SmsApiGatewayForm(request.POST, instance=s)
    if form.is_valid():
        form.save()
        audit_record(AuditAction.UPDATE, s, actor=request.user, extra={"section": "sms_api_gateway"})
        messages.success(request, "تم حفظ إعدادات بوابة API.")
    else:
        messages.error(request, next(iter(form.errors.values()), ["تعذّر الحفظ."])[0])
    return redirect(_settings_url(TAB_API))


@management_required
@require_POST
def api_gateway_test(request):
    """Call the configured refresh API with a phone and return the parsed reply."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        payload = {}
    phone = str(payload.get("phone") or "").strip()
    if not is_valid_number(phone):
        return JsonResponse({"ok": False, "error": "رقم غير صالح (050-055)."}, status=400)
    outcome = call_refresh_api(phone, request=request)
    return JsonResponse(
        {
            "ok": True,
            "http_status": outcome.http_status,
            "status_code": outcome.status_code,
            "title": outcome.title,
            "body": outcome.body,
            "error": outcome.error,
        }
    )


@management_required
@require_POST
def inbound_simulate(request):
    """Dry-run the inbound pipeline; show the would-be reply without sending."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        payload = {}
    from_number = str(payload.get("from") or "").strip()
    text = str(payload.get("text") or "")
    if not from_number:
        return JsonResponse({"ok": False, "error": "أدخل رقم المُرسِل."}, status=400)
    result = process_inbound(
        from_number=from_number, raw_text=text, request=request, simulate=True
    )
    return JsonResponse(
        {
            "ok": True,
            "state": result.state,
            "extracted_number": result.extracted_number,
            "refresh_status": result.refresh_status,
            "reply_text": result.reply_text,
        }
    )


@management_required
@require_POST
def reply_policy_save(request):
    _ensure_reply_policies()
    for policy in SmsReplyPolicy.objects.select_related("status").all():
        enabled = bool(request.POST.get(f"reply_enabled_{policy.status_id}"))
        override = (request.POST.get(f"override_{policy.status_id}") or "").strip()
        policy.reply_enabled = enabled
        policy.message_override = override
        policy.save(update_fields=["reply_enabled", "message_override", "updated_at"])
    audit_record(AuditAction.UPDATE, SmsGatewaySettings.load(), actor=request.user,
                 extra={"section": "sms_reply_policies"})
    messages.success(request, "تم حفظ سياسات الردود.")
    return redirect(_settings_url(TAB_REPLIES))


# --------------------------------------------------------------------------- devices


@management_required
@require_POST
def device_create(request):
    form = SmsDeviceForm(request.POST)
    if not form.is_valid():
        messages.error(request, next(iter(form.errors.values()), ["تعذّر الإضافة."])[0])
        return redirect(_settings_url(TAB_DEVICES))
    raw_token = secrets.token_urlsafe(48)
    device = form.save(commit=False)
    device.token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    device.token_prefix = raw_token[:8]
    if request.user.is_authenticated:
        device.created_by = request.user
    device.save()
    audit_record(AuditAction.CREATE, device, actor=request.user)
    messages.success(
        request,
        f"تم إنشاء الجهاز. التوكن (يظهر مرة واحدة فقط): {raw_token}",
    )
    return redirect(_settings_url(TAB_DEVICES))


@management_required
@require_POST
def device_update(request, pk: int):
    device = get_object_or_404(SmsGatewayDevice, pk=pk)
    form = SmsDeviceForm(request.POST, instance=device)
    if form.is_valid():
        form.save()
        audit_record(AuditAction.UPDATE, device, actor=request.user)
        messages.success(request, "تم تحديث الجهاز.")
    else:
        messages.error(request, next(iter(form.errors.values()), ["تعذّر التحديث."])[0])
    return redirect(_settings_url(TAB_DEVICES))


@management_required
@require_POST
def device_regenerate_token(request, pk: int):
    device = get_object_or_404(SmsGatewayDevice, pk=pk)
    raw_token = secrets.token_urlsafe(48)
    device.token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    device.token_prefix = raw_token[:8]
    device.save(update_fields=["token_hash", "token_prefix"])
    audit_record(AuditAction.UPDATE, device, actor=request.user, extra={"action": "regenerate_token"})
    messages.success(request, f"تم توليد توكن جديد (يظهر مرة واحدة): {raw_token}")
    return redirect(_settings_url(TAB_DEVICES))


@management_required
@require_POST
def device_reactivate(request, pk: int):
    device = get_object_or_404(SmsGatewayDevice, pk=pk)
    device.auto_paused_at = None
    device.consecutive_failures = 0
    device.is_active = True
    device.save(update_fields=["auto_paused_at", "consecutive_failures", "is_active"])
    audit_record(AuditAction.UPDATE, device, actor=request.user, extra={"action": "reactivate"})
    messages.success(request, "تم إعادة تفعيل الجهاز.")
    return redirect(_settings_url(TAB_DEVICES))


@management_required
@require_POST
def device_delete(request, pk: int):
    device = get_object_or_404(SmsGatewayDevice, pk=pk)
    audit_record(AuditAction.DELETE, device, actor=request.user)
    device.delete()
    messages.success(request, "تم حذف الجهاز.")
    return redirect(_settings_url(TAB_DEVICES))


# --------------------------------------------------------------------------- access rules


@management_required
@require_POST
def access_rule_create(request):
    form = SmsAccessRuleForm(request.POST)
    if form.is_valid():
        rule = form.save()
        audit_record(AuditAction.CREATE, rule, actor=request.user)
        messages.success(request, "تمت إضافة القاعدة.")
    else:
        messages.error(request, next(iter(form.errors.values()), ["تعذّر الإضافة."])[0])
    return redirect(_settings_url(TAB_REPLIES))


@management_required
@require_POST
def access_rule_delete(request, pk: int):
    rule = get_object_or_404(SmsAccessRule, pk=pk)
    audit_record(AuditAction.DELETE, rule, actor=request.user)
    rule.delete()
    messages.success(request, "تم حذف القاعدة.")
    return redirect(_settings_url(TAB_REPLIES))


# --------------------------------------------------------------------------- reports


def _heartbeat_alerts(settings_obj: SmsGatewaySettings) -> list[SmsGatewayDevice]:
    minutes = settings_obj.heartbeat_alert_minutes or 0
    if not minutes:
        return []
    cutoff = timezone.now() - timedelta(minutes=minutes)
    return list(
        SmsGatewayDevice.objects.filter(is_active=True).filter(
            last_seen_at__isnull=True
        )
        | SmsGatewayDevice.objects.filter(is_active=True, last_seen_at__lt=cutoff)
    )


@management_required
def reports_list(request):
    s = SmsGatewaySettings.load()
    start_today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    inbound_qs = InboundSms.objects.select_related("device", "refresh_log").all()
    q = (request.GET.get("q") or "").strip()
    state = (request.GET.get("state") or "").strip()
    if q:
        inbound_qs = inbound_qs.filter(from_number__icontains=q)
    if state:
        inbound_qs = inbound_qs.filter(state=state)
    page_obj = paginate_request(request, inbound_qs)

    inbound_today = InboundSms.objects.filter(received_at__gte=start_today)
    outbound_today = OutboundSms.objects.filter(created_at__gte=start_today)
    sent_today = outbound_today.filter(state=OutboundSms.State.SENT).count()
    queued = OutboundSms.objects.filter(
        state__in=[OutboundSms.State.QUEUED, OutboundSms.State.CLAIMED]
    ).count()
    dead_letters = list(
        OutboundSms.objects.filter(state=OutboundSms.State.DEAD_LETTER).order_by("-created_at")[:50]
    )
    processed_today = inbound_today.filter(state=InboundSms.State.PROCESSED).count()
    received_today = inbound_today.count()
    success_rate = round((processed_today / received_today) * 100) if received_today else 0

    per_device = list(
        OutboundSms.objects.filter(state=OutboundSms.State.SENT, sent_at__gte=start_today)
        .values("claimed_by__name")
        .annotate(n=Count("id"))
        .order_by("-n")
    )

    ctx = {
        "title": "تقارير تحديث الرسائل",
        "page_obj": page_obj,
        "q": q,
        "selected_state": state,
        "states": InboundSms.State.choices,
        "stats": {
            "received_today": received_today,
            "processed_today": processed_today,
            "sent_today": sent_today,
            "queued": queued,
            "success_rate": success_rate,
        },
        "per_device": per_device,
        "dead_letters": dead_letters,
        "heartbeat_alerts": _heartbeat_alerts(s),
    }
    return render(request, "sms_gateway/reports.html", ctx)


@management_required
@require_POST
def outbound_resend(request, pk: int):
    ob = get_object_or_404(OutboundSms, pk=pk)
    ob.state = OutboundSms.State.QUEUED
    ob.claimed_by = None
    ob.claimed_at = None
    ob.attempts = 0
    ob.error = ""
    ob.save(update_fields=["state", "claimed_by", "claimed_at", "attempts", "error"])
    messages.success(request, "تمت إعادة جدولة الرسالة للإرسال.")
    return redirect(reverse("sms_gateway:reports"))
