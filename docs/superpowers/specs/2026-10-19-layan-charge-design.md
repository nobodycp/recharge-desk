# Layan Charge ("شحن الرقم") — Design

Approved with the user on 2026-10-19 (Arabic discussion). Builds on the existing
employee charge path used by Sky; Layan only adds a provider adapter behind the
same button and job machinery.

## Goal

Sales staff charge a Layan line from the employee entry screen the same way as
Sky: pick the product, press **شحن**, and the provider renews the line after a
price match. Active lines are extended; disconnected lines are activated.
Monthly products use a regular duration; 3‑month products use that number's
promo sale on Layan.

## Discovery (live probe on 0512932676, 2026-10-19)

| Call | Result |
|---|---|
| `CheckSubscription` status | `غير فعال` → use **ActivateLine** |
| `Packages/GetPackageDuration` | Day-like ints labelled as months (`شهر`=30, `شهرين`=60, `ثلاثة أشهر`=90, …) |
| `Deals/GetCostBeforeActivate` `{Duration:30}` | `30` |
| `Subscribtions/GetSalesByNumber/{phone}` | Promo list; e.g. id `cf68bc21-…`, name «حزمة 500 جيجا 2026 / شهري / 80₪ / ثلاثة اشهر», `duration`: «ثلاثة اشهر» |
| `Deals/GetCostBeforeActivate` `{SaleId}` | `80` (quoted only; not charged) |
| `Subscribtions/GetSaleDuration/{phone}` | **401** for this agent account — do **not** use |
| `Deals/ActivateLine` month charge | HTTP 200 body `سيتم تشغيل الرقم في الخلفيه` — this **is** success; no follow-up API |

Portal action map (from Layan JS): active → `extendSubcription` / `Deals/Extend`;
disconnected → `activateLine` / `Deals/ActivateLine`. Confirm texts differ in the
portal; **our app does not add a Layan-only confirm** (same UX as Sky).

## User decisions

1. **No special confirm** before Layan charge. Same as Sky (only the existing
   same-day repeat-charge confirm, if any).
2. **Status at charge time** from Layan: `فعال` → Extend; otherwise (e.g.
   `غير فعال` / disconnected) → ActivateLine.
3. **Product duration drives package type:**
   - Monthly (`base_days == 30`, or generally a non‑promo month package) →
     regular package with `Duration` matching Layan months-as-days
     (30 for one month).
   - 3 months (`base_days == 90`) → promo: pick the sale from
     `GetSalesByNumber` whose duration is three months, send `SaleId`.
4. **Price match** like Sky: `GetCostBeforeActivate` quote must equal
   `sale.cost_price_snapshot` (0.01). On mismatch: employee-only clear error
   «السعر غير مطابق», no charge sent, same failure path as Sky.
5. **Success body** `سيتم تشغيل الرقم في الخلفيه` (or equivalent success text)
   means done. **No** `check_charge` / polling follow-up for Layan.
6. **`UserPaid=true`** always on employee charge. Not shown in the UI.
7. **`endDate`** on ActivateLine: from the subscription's current end date when
   present; omit when missing.
8. After ship, staff can try live once Layan `charge_enabled` is on for the
   company and the employee may charge.

## Flow

1. Employee submits `action=charge` on the existing entry form (company must
   support charge; employee needs `can_provider_charge` unless management).
2. Existing sale + `ProviderJob` + Celery `run_charge_job` path runs unchanged.
3. `LayanAdapter.charge(ctx)`:
   1. Ensure a usable Layan session (reuse stored JWT; login only if needed).
   2. Load subscription + usage for `ctx.phone`. Missing line → `not_found`.
   3. Decide action from usage/subscription status (`فعال` → Extend, else
      Activate).
   4. Resolve package:
      - If `ctx.duration_days == 90` (3‑month product):
        `GetSalesByNumber` → sale whose `duration` indicates three months
        (Arabic «ثلاثة» / value 90 if present). None → `period_unavailable`
        (or a clear permanent error) without charging.
      - Else: `Duration = ctx.duration_days` (30 for monthly). Unsupported
        duration → `period_unavailable`.
   5. `GetCostBeforeActivate` with `Number` + `Duration` and/or `SaleId`.
      Compare to `ctx.expected_cost`; mismatch → `price_mismatch`.
   6. POST `Deals/Extend` or `Deals/ActivateLine` with
      `{Number, Duration, SaleId, UserPaid: true}` and `endDate` when
      activating and known.
   7. HTTP 200 with the known success text (or empty success) →
      `ChargeResult.SUCCESS`. Prefer storing a provider ref if the body
      includes one; otherwise message text is enough. No follow-up verify.
4. Existing `apply_outcome` updates the sale / reverses on hard failure as today.

## Components

| Layer | Change |
|---|---|
| `LayanAdapter` | Add `Capability.CHARGE`; implement `charge`. `check_charge` may stay unsupported / unused. |
| `LayanClient` | Methods: status/subscription helpers already used by number info; add `get_cost_before_activate`, `get_sales_by_number`, `extend`, `activate_line`. |
| `providers.service` | No API change; gate already uses `service_state(CHARGE)`. |
| `sales.charging` / entry UI | No new button. Enabling Layan charge is `ProviderConnection.charge_enabled` (+ credentials). |
| Locale | Reuse Sky price-mismatch employee message where possible. |

## Errors (employee-facing, same style as Sky)

| Code | When |
|---|---|
| `not_found` | Number missing at Layan |
| `period_unavailable` | No matching duration / no 3‑month promo for the number |
| `price_mismatch` | Quote ≠ our cost; clear employee message, not a vague admin dump |
| `login_failed` / session | Auth problems |
| `provider_error` / connection | Unexpected HTTP or transport failures |

## Testing

- **Unit:** status → Extend vs ActivateLine; month `Duration` vs 90‑day `SaleId`
  selection from a sales list; price match / mismatch; missing promo; success
  body accepted without follow-up.
- **Light integration (fake Layan adapter):** automated tests that do **not**
  call the real portal. A fake adapter returns success or `price_mismatch` so
  we prove entry → job → sale `charge_status` still works when the company
  provider is Layan. Live trials are manual after deploy.

## Out of scope

- Changing Sky charge behaviour.
- New employee UI for picking promo vs regular (automatic from product days).
- Using `GetSaleDuration` (401 on the working agent account).
- Manual Layan charges (`AddChargeForSubscription`) or balance top-ups.
- Package change / SIM change portal actions.
