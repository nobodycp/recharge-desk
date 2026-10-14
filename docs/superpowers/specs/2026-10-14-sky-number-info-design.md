# Sky Number Info ("فحص الرقم") — Design (Sky phase 2)

Approved with the user on 2026-10-14 (Arabic discussion). Phase 1 (operations log,
"تقارير المزودين") is live. Phase 3 (charge with price match + follow-up) is separate.

## Goal

Sales staff type a number and press **«فحص الرقم»** to see the line's live data from
Sky (network, SIM type, status, package, expiry with days left, usage, ICCID) plus the
**sale price from our system**. Every check is one row in "تقارير المزودين" with a
named Arabic error on failure.

## Discovery (read-only lookups on production, 2026-10-14)

`GetSubscribers4TasksByPatternV3` (already called first by every Sky refresh) returns
everything; no new Sky API is needed. Example row (0525737739, values shortened):

| Sky field | Example | Use |
|---|---|---|
| `ne_id` / `msisdn` | `0525737739` / `972525737739` | exact-match check |
| `mno_name` | سلكوم | الشركة |
| `esim` | `esim` (eSIM) / `""` (physical) | نوع الشريحة |
| `status_name` | مفعل | الحالة |
| `rate_plan_name` | حملة رمضان 400 جيجا سلكوم | اسم الحزمة |
| `rate_plan_end_date_time` | `13/11/2026 23:30` | تاريخ الانتهاء (user chose this over `rate_plan_end_date` = 14/11) |
| `total_data_in_billing_cycle` / `allocated_mb` | `6.08` (GB) / `409600` (MB) | إنترنت 6.08 من 400 جيجا |
| `total_voice_in_billing_cycle` / `allocated_minutes` | `0` / `5000` | دقائق |
| `total_sms_in_billing_cycle` / `allocated_sms` | `0` / `5000` | رسائل |
| `utilization` | `1.52` (= 6.08/400 %) | نسبة الاستخدام |
| `iccid` | 8997… | رقم الشريحة |
| `rate_plan_price` | `29` | **hidden**; equals our `Product.cost_price` → phase 3 match |

Not used: `subscriber_name`, `auto_renew`, `commision`, `subscriber_price`,
`sub_customer_*`, ids, counters, commitment and secondary-SIM fields.

`search_subscriber` falls back to the first row when no row matches exactly; number
info must **not** — it requires `ne_id == phone` (or `msisdn` ending in `phone[1:]`),
else "not found".

## User decisions

- Page: the employee "تحديث الرقم" page becomes **«فحص الأرقام»** with two buttons
  under the number field: **«تحديث الرقم»** (unchanged behaviour) and **«فحص الرقم»**.
- Sale price = `default_sell_price` of the **product of the number's last sale** in our
  system (same "last sale" rule refresh routing already uses). Shown with the package
  name and the last-sale date. Sky's price is read and kept, never shown.
- Expiry = `rate_plan_end_date_time`; days left computed by us
  (`ends_at.date() − today`, project timezone `Asia/Jerusalem`).
- Usage = used of allocated per type + utilization %.
- Visible to employees and management; the page stays behind the existing
  `AppSettings.sales_show_refresh_phone` switch.
- Available when the company's provider connection is enabled (master switch +
  credentials); no new per-service switch.
- No caching: each press reads Sky; a check already in flight for the same number is
  joined, not duplicated.

## Flow

1. Validate the number (`phone_refresh.validation.PHONE_RE`), else "رقم غير صحيح".
2. Last sale: `Sale.objects.filter(reference_number=phone).order_by("-created_at")`.
   None → «لا توجد عملية بيع سابقة لهذا الرقم» (no row, no provider call).
3. `service_state(company, Capability.NUMBER_INFO)`:
   - `unsupported` → «فحص الرقم غير متاح لهذا المزوّد» (no row).
   - `off` / `not_configured` (company has a provider key) → one operations row
     (`service_off` / `not_configured`) and the named message; no job.
4. Join an in-flight `number_info` job for the phone (window = `refresh_wait_seconds`)
   or create one and enqueue `providers.tasks.run_number_info_job` on commit. Enqueue
   failure → job failed, «الخدمة مشغولة حالياً، حاول بعد قليل», **no row** (no provider
   call, as in the phase-1 fix).
5. Worker: `providers.service.number_info(...)` → `run_authenticated` (logs in if the
   session is missing, once, cluster-wide) → `adapter.number_info(phone)`; transient
   errors retried like refresh. Records exactly one row. Result stored on the job.
6. The page polls; done → the card; failed → the named message; still running after
   `refresh_wait_seconds` → «الطلب ما زال قيد المعالجة، حاول بعد دقيقة».

## Messages

Service label for `number_info` becomes **«فحص الرقم»** (was «بيانات الرقم»; no row
used it yet); success text «تمّ فحص الرقم». Failures follow phase 1
(`فشل فحص الرقم — <السبب>`):

| Case | error_code | Message |
|---|---|---|
| login failed / session lost after login | `login_failed` | فشل فحص الرقم — خطأ تسجيل الدخول |
| login busy | `login_busy` | فشل فحص الرقم — تسجيل الدخول قيد التنفيذ، حاول بعد قليل |
| connection / proxy / budget | `connection` | فشل فحص الرقم — تعذّر الاتصال بالمزوّد |
| no exact subscriber | `not_found` (new cause «الرقم غير موجود لدى المزوّد») | فشل فحص الرقم — الرقم غير موجود لدى المزوّد |
| connection off | `service_off` | فشل فحص الرقم — الخدمة موقوفة |
| credentials missing | `not_configured` | فشل فحص الرقم — بيانات الحساب ناقصة |
| other provider error | `provider_error` | فشل فحص الرقم — خطأ في عملية فحص الرقم |
| crash | `internal` | فشل فحص الرقم — خطأ داخلي |

Rows: `service="number_info"`, `source="employee"` (management users → `management`),
`user_id`, `job_id`, `attempts`, `duration_ms`, `sale_id` = last sale id;
`detail` = scrubbed error text on failure, empty on success.

## Card (server-rendered, auto-escaped)

| Label | Value |
|---|---|
| الشركة | `mno_name` |
| نوع الشريحة | «شريحة إلكترونية» if `esim` == "esim" (case-insensitive) else «شريحة عادية» |
| الحالة | `status_name` |
| اسم الحزمة | `rate_plan_name` |
| تاريخ الانتهاء | `13/11/2026 — متبقي 30 يوم`; 0 → «تنتهي اليوم»; past → «منتهية» |
| الاستهلاك | «إنترنت 6.08 من 400 جيجا · دقائق 0 من 5000 · رسائل 0 من 5000 · نسبة الاستخدام 1.52%» |
| رقم الشريحة | `iccid` |
| سعر البيع | `60 ₪ — Cellcom — رمضان (آخر بيعة 14/10/2026)` |

Missing/unparseable Sky values show «—»; a bad value never fails the whole check.

## Components

- `providers/contracts.py`: `Capability.NUMBER_INFO`; `NumberInfo` dataclass with
  JSON round-trip (`to_dict` / `from_dict`); `BaseAdapter.number_info(phone)`.
- `providers/adapters/sky/number_info.py`: pure `parse_number_info(row) -> NumberInfo`
  and `matches_phone(row, phone)`; `SkyAdapter.number_info` + capability.
- `providers/operations.py`: label/success text change, `not_found` cause.
- `providers/service.py`: `number_info(...) -> NumberInfoResult(info, classified)`.
- `providers/models.py`: `ProviderJob.KIND_NUMBER_INFO` (+ choices migration).
- `providers/tasks.py`: `run_number_info_job`; `purge_old_jobs` also purges
  `number_info` jobs (same 30-day retention).
- `sales/number_check.py`: submit + poll + card context (sales may import providers;
  providers never imports sales).
- `sales/views/employee.py`, `sales/urls.py`: `employee_check_phone` (POST JSON),
  `employee_check_status` (GET JSON with server-rendered card HTML).
- `templates/sales/employee_refresh_number.html` (renamed page, two buttons),
  `templates/sales/partials/number_check_card.html`, `static/js/employee-check-number.js`,
  bottom-nav label.

## Out of scope

Charge and price match (phase 3); public customer page; showing Sky price or
commission; caching; a stuck-job reaper for `number_info` (the poll times out on its
own and the in-flight window bounds joining).

## Testing

Parser on both real rows (names/ICCIDs anonymised); exact-match; each failure message;
one row per check including login-then-success; gate rows; enqueue failure → no row;
poll states; card rendering incl. «—», expired, no-sale; escaping; refresh button
behaviour unchanged; JS polling (node test like `tests/js/employee_refresh_polling.test.js`).
