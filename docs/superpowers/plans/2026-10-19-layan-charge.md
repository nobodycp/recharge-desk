# Layan Charge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Charge a Layan line from the existing employee **شحن** button: price-match, then Extend (active) or ActivateLine (disconnected); month = `Duration`, 3 months = promo `SaleId`.

**Architecture:** Add `Capability.CHARGE` and `LayanAdapter.charge` behind the current `sales.charging` / Celery job path. Pure helpers in `providers/adapters/layan/charge.py` decide action and package; `LayanClient` talks to the portal. No new UI and no `check_charge` follow-up.

**Tech Stack:** Django 4.2, Python 3.9 (`.venv`, `from __future__ import annotations`), Celery eager + fakeredis in tests, existing `ChargeContext` / `ChargeOutcome`.

**Spec:** `docs/superpowers/specs/2026-10-19-layan-charge-design.md`

## Global Constraints

- `providers` never imports `sales`.
- Reuse Sky mismatch cause: `PermanentError("price_mismatch")` → employee message «السعر غير مطابق».
- `UserPaid=true` always; `endDate` only on Activate when subscription has one.
- Success text containing «سيتم تشغيل الرقم في الخلفيه» (or HTTP 200 with empty/known success) is final — no `check_charge`.
- Do not call `GetSaleDuration` (401 on the working agent).
- No Layan-only confirm dialog.
- Tests: `.venv/bin/python manage.py test <label>`; stage explicit paths; never push unless asked.

## Review Focus

- Quote must run **before** Extend/Activate so a mismatch never charges. Test in Task 2.
- Status `فعال` only → Extend; any other / missing → ActivateLine. Test in Task 1.
- `duration_days == 90` with no three-month sale → `period_unavailable`, no POST. Test in Task 1.
- Company with Layan + `charge_enabled` + credentials → `service_state(CHARGE)` active. Test in Task 3.
- Fake Layan charge adapter through employee entry updates `charge_status` like Sky. Test in Task 3.

## File map

| File | Responsibility |
|---|---|
| `providers/adapters/layan/charge.py` | Pure helpers: active?, pick 3‑month sale, parse cost quote, success body?, build payloads |
| `providers/adapters/layan/client.py` | HTTP: cost, sales list, extend, activate |
| `providers/adapters/layan/adapter.py` | `CHARGE` capability + `charge(ctx)` |
| `providers/tests/test_layan_charge.py` | Unit tests for helpers + adapter |
| `providers/tests/test_service_state.py` / mapping | Layan charge gate |
| `sales/tests_charge_ui.py` or small new test | Light entry→job with fake Layan |

---

### Task 1: Pure charge helpers + client endpoints

**Files:**
- Create: `providers/adapters/layan/charge.py`
- Modify: `providers/adapters/layan/client.py`
- Test: `providers/tests/test_layan_charge.py` (helpers + client request shape)

**Interfaces:**
- `is_active_status(status: str) -> bool` — true only when stripped status is exactly `فعال` (or case-insensitive ASCII `active` if ever returned); `غير فعال` → false.
- `pick_three_month_sale(sales: list[dict]) -> dict | None` — first sale whose `duration` text contains `ثلاثة` or whose numeric duration is 90; else None.
- `parse_cost_quote(body) -> Decimal | None` — bare number / numeric string / JSON number → quantized `0.01`; non-finite / missing → None.
- `is_charge_success(status_code: int, body: str | dict | None) -> bool` — 200 and (empty body, or text/JSON string containing `سيتم تشغيل الرقم في الخلفيه`, or other non-error 200 text that is not an HTML error page). Prefer: 200 + (success phrase in text OR empty/whitespace OR plain success string without `خطأ`).
- `costs_match(quoted: Decimal | None, expected: Decimal) -> bool` — same rule as Sky (`quoted == expected.quantize(0.01)`); may import from `providers.adapters.sky.topup` **or** duplicate a 3-line helper in `layan/charge.py` to avoid Sky coupling — prefer a tiny local `_money_match` in `layan/charge.py`.
- `LayanClient.get_sales_by_number(phone: str) -> list[dict]`
- `LayanClient.get_cost_before_activate(phone: str, *, duration: int | None, sale_id: str | None) -> Any` — POST `Deals/GetCostBeforeActivate`
- `LayanClient.extend_line(phone, *, duration, sale_id, user_paid: bool = True) -> tuple[int, str]`
- `LayanClient.activate_line(phone, *, duration, sale_id, end_date: str | None, user_paid: bool = True) -> tuple[int, str]`

- [ ] **Step 1: Failing tests** for helpers with the probe fixtures (`duration: "ثلاثة اشهر"`, cost bodies `30` / `"80"`, status `فعال` / `غير فعال`, success body text).
- [ ] **Step 2: Run** `.venv/bin/python manage.py test providers.tests.test_layan_charge` → FAIL.
- [ ] **Step 3: Implement helpers + client methods** (mock `requests` like `test_layan_client.py`).
- [ ] **Step 4: Run** same tests → PASS.
- [ ] **Step 5: Commit** `feat(providers): Layan charge helpers and portal calls`.

### Task 2: `LayanAdapter.charge`

**Files:**
- Modify: `providers/adapters/layan/adapter.py`
- Test: `providers/tests/test_layan_charge.py` (adapter section)

**Interfaces:**
- `capabilities` includes `Capability.CHARGE` (keep `login_capabilities` = reconcile + number_info only; charge uses the same stored session / login path via `_stored_client` + `run_authenticated` at the service layer).
- `charge(self, ctx: ChargeContext) -> ChargeOutcome`:
  1. `_stored_client()`; `fetch_subscription` + match phone → else `PermanentError("not_found")`.
  2. `fetch_usage` for status; `is_active_status` → extend else activate.
  3. If `ctx.duration_days == 90`: `get_sales_by_number` → `pick_three_month_sale` → else `period_unavailable`; set `sale_id`, `duration=None`.
     Else: `duration = ctx.duration_days` (require truthy int, else `period_unavailable`); `sale_id=None`.
  4. Quote = `parse_cost_quote(get_cost_before_activate(...))`; if not `_money_match(quote, ctx.expected_cost)` → `PermanentError("price_mismatch")` **before** extend/activate.
  5. Call extend or activate (`UserPaid=True`; activate passes `endDate` from subscription when present).
  6. If `is_charge_success` → `ChargeOutcome(SUCCESS, provider_ref="", provider_cost=quote, plan_name=package name if any)`; else `PermanentError("provider_error")` or map known failures.
- Session errors → `AuthRequired` via existing `_call` / patterns.
- Do **not** implement `check_charge` (leave `NotImplementedError`).

- [ ] **Step 1: Failing adapter tests** (patch client methods):
  - active + 30 days + matching cost → Extend called, not Activate; SUCCESS.
  - inactive + 30 days → Activate with `endDate`.
  - 90 days + sales list with three-month sale + cost 80 matching → SaleId sent, Duration null/omitted.
  - 90 days + empty sales → `period_unavailable`, no extend/activate.
  - cost mismatch → `price_mismatch`, no extend/activate.
  - success body «سيتم تشغيل الرقم في الخلفيه» → SUCCESS.
- [ ] **Step 2: Run** tests → FAIL.
- [ ] **Step 3: Implement `charge`.**
- [ ] **Step 4: Run** `providers.tests.test_layan_charge` → PASS.
- [ ] **Step 5: Commit** `feat(providers): charge Layan lines with price match`.

### Task 3: Gate + light employee-path integration

**Files:**
- Modify: `providers/tests/test_service_state.py` and/or `providers/tests/test_provider_mapping.py`
- Modify or create: charge UI/integration test that uses a Layan company (or patches `LayanAdapter.charge`) — prefer extending patterns in `sales/tests_charge_ui.py` / `sales/tests_charging.py` with `provider_key="layan"` and patched adapter, **or** register a thin fake only if patching the real adapter is awkward.
- Confirm `test_layan_number_info` / mapping still green if they assert capabilities.

**Interfaces:**
- With credentials + `charge_enabled=True`, `service_state(layan_company, Capability.CHARGE).code == "active"`.
- Without credentials → `not_configured` (charge needs login session credentials like number info — **wait**: spec says charge uses stored session; `login_capabilities` currently excludes CHARGE. Decision locked here: **charge must be able to login when session missing**, so either add `CHARGE` to `login_capabilities` or rely on `_stored_client` raising `AuthRequired` and `run_authenticated` calling `login`. Prefer **add `Capability.CHARGE` to `login_capabilities`** so `service_state` requires credentials before offering charge (same as number info).
- Integration: create sale via charge action on a Layan company with patched `LayanAdapter.charge` returning SUCCESS → sale `charge_status` success; patched `price_mismatch` → failed + employee-visible message path unchanged.

- [ ] **Step 1: Failing gate + one entry-path test.**
- [ ] **Step 2: Run** those tests → FAIL.
- [ ] **Step 3: Set `login_capabilities` to include `CHARGE`; fix any capability assertions.**
- [ ] **Step 4: Run** `providers.tests.test_layan_charge providers.tests.test_service_state sales.tests_charging` (or the chosen UI module) → PASS.
- [ ] **Step 5: Commit** `feat(providers): enable Layan charge behind the employee path`.

---

## Self-review notes

- Spec: no special confirm, GetSalesByNumber for promo, price match, success text, UserPaid, endDate, no check_charge — all tasked.
- `login_capabilities` + CHARGE called out in Task 3 so the gate matches “credentials required”.
- Review Focus lines each have a owning test.
