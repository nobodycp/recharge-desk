"""Device-facing API: inbound webhook, outbox poll, delivery report.

All endpoints authenticate with ``Authorization: Bearer <token>`` matched
against a :class:`SmsGatewayDevice` token hash. They are CSRF-exempt
(machine clients) and return JSON.
"""
from __future__ import annotations

import hashlib
import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.http_utils import get_client_ip
from sms_gateway.models import SmsGatewayDevice
from sms_gateway.services.outbox import claim_for_device, mark_delivery, pending_delete_ids
from sms_gateway.services.processing import process_inbound


def _authenticate(request) -> SmsGatewayDevice | None:
    header = request.META.get("HTTP_AUTHORIZATION") or ""
    if not header.lower().startswith("bearer "):
        return None
    raw = header[7:].strip()
    if not raw:
        return None
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    device = SmsGatewayDevice.objects.filter(token_hash=digest, is_active=True).first()
    if device is None:
        return None
    SmsGatewayDevice.objects.filter(pk=device.pk).update(last_seen_at=timezone.now())
    return device


def _load_json(request) -> dict:
    if "application/json" in (request.content_type or "").lower():
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    return {k: request.POST.get(k) for k in request.POST.keys()}


@csrf_exempt
@require_POST
def sms_inbound(request):
    device = _authenticate(request)
    if device is None:
        return JsonResponse({"error": "invalid_token"}, status=401)

    data = _load_json(request)
    from_number = str(data.get("from") or data.get("from_number") or "").strip()
    text = str(data.get("text") or data.get("message") or "")
    device_msg_id = str(data.get("device_msg_id") or data.get("id") or "").strip()

    if not from_number:
        return JsonResponse({"error": "missing_from"}, status=400)

    result = process_inbound(
        from_number=from_number,
        raw_text=text,
        device=device,
        device_msg_id=device_msg_id,
        request=request,
    )
    return JsonResponse(
        {
            "ok": True,
            "state": result.state,
            "extracted_number": result.extracted_number,
            "queued_reply": bool(result.outbound_id),
        }
    )


@csrf_exempt
@require_GET
def sms_outbox(request):
    device = _authenticate(request)
    if device is None:
        return JsonResponse({"error": "invalid_token"}, status=401)

    try:
        limit = int(request.GET.get("limit") or 10)
    except ValueError:
        limit = 10
    limit = max(1, min(limit, 50))

    messages = claim_for_device(device, limit=limit)
    return JsonResponse(
        {
            "ok": True,
            "messages": [
                {"id": m.pk, "to": m.to_number, "body": m.body} for m in messages
            ],
            "delete_ids": pending_delete_ids(device),
        }
    )


@csrf_exempt
@require_POST
def sms_delivery(request):
    device = _authenticate(request)
    if device is None:
        return JsonResponse({"error": "invalid_token"}, status=401)

    data = _load_json(request)
    sent_ids = data.get("sent") or []
    failed = data.get("failed") or []
    deleted = data.get("deleted") or []

    # Be permissive about types coming from various gateway apps.
    if isinstance(sent_ids, str):
        sent_ids = [s for s in sent_ids.split(",") if s.strip()]
    try:
        sent_ids = [int(x) for x in sent_ids]
    except (TypeError, ValueError):
        sent_ids = []
    if not isinstance(failed, list):
        failed = []
    if isinstance(deleted, str):
        deleted = [d for d in deleted.split(",") if d.strip()]
    if not isinstance(deleted, list):
        deleted = []

    result = mark_delivery(
        device,
        sent_ids=sent_ids,
        failed=failed,
        deleted_device_msg_ids=[str(d) for d in deleted],
    )
    return JsonResponse({"ok": True, **result})
