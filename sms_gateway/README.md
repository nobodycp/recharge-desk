# SMS Gateway — device protocol

This app turns subscriber SMS into number-refresh replies. An Android
gateway phone forwards incoming SMS to the server, the server extracts a
050-055 number, calls the configurable refresh API (Settings → بوابة API),
and queues a reply that any registered device sends back to the sender.

The protocol is app-agnostic: use SMSsync, "SMS Gateway for Android", or a
Tasker/MacroDroid HTTP profile. Each device authenticates with its own
token (created in Settings → الأجهزة).

Auth header on every request:

```
Authorization: Bearer <device-token>
```

## 1. Inbound (device → server), on each received SMS

`POST /sms-gateway/api/inbound/`

```json
{ "from": "0555544071", "text": "حدثلي 0555544071", "device_msg_id": "12" }
```

Response:

```json
{ "ok": true, "state": "processed", "extracted_number": "0555544071", "queued_reply": true }
```

## 2. Outbox poll (device → server), every few seconds

`GET /sms-gateway/api/outbox/?limit=10`

```json
{
  "ok": true,
  "messages": [ { "id": 99, "to": "0555544071", "body": "تم التحديث" } ],
  "delete_ids": ["12"]
}
```

The device sends each `body` to `to`, and deletes the local SMS whose ids
are in `delete_ids`.

## 3. Delivery report (device → server)

`POST /sms-gateway/api/delivery/`

```json
{ "sent": [99], "failed": [{ "id": 100, "error": "no balance" }], "deleted": ["12"] }
```

## Behavior

- Reply goes to the SENDER, not the refreshed number.
- Numbers accepted: `^05[0-5]\d{7}$` (050-055).
- Multiple devices: replies come from the highest-priority device that is
  active, `can_send`, and currently polling. Unconfirmed claims return to
  the queue after `claim_timeout_seconds`. Devices auto-pause after
  `auto_pause_threshold` consecutive failures and can have a daily send cap.
- Full control of replies in Settings: service on/off, master replies
  switch, per-status reply toggles, test number, block/allow lists,
  per-sender and global daily caps.
- Old logs are purged by `python manage.py sms_purge_logs` (cron).
