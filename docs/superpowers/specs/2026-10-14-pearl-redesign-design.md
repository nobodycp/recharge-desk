# Pearl redesign — whole-system visual identity, UX and speed

Date: 2026-10-14
Status: design approved in chat; this spec is the reference for every phase.

## 1. Intent

The owner wants the whole system (management + employee) to feel modern, premium and
practical, and to feel faster — with mobile as the top priority, desktop not neglected.

Agreed in the brainstorming session (visual companion):

- Identity: **Pearl Premium** — pearl white, deep ink black, one fiery orange accent.
  Chosen over "Aurora Glass" and "Neon Night".
- Applies to **every page and sub-page** (sales, employee entry, reports, settings, …) and
  to **every future page**.
- **No logic changes.** Data, filters (day / week / month …), flows, permissions, URLs and
  form fields stay exactly as they are. Only look, layout and interaction polish change.
- Login page gets special care: split screen on desktop, highly interactive on mobile
  (mockup approved as-is).
- Employee sale entry must stay **one screen, no scrolling** (as the current page is),
  using the approved compact Pearl layout (light + dark). Small tweaks after it runs.
- Out of scope: the public phone-refresh page (`phone_refresh/public_refresh.html`)
  keeps its current look.

Success criteria:

1. Every page renders in Pearl (light and dark) with no leftover legacy styling.
2. Employee entry fits one screen on an iPhone SE–sized viewport (375×667) with the
   bottom bar and save buttons visible.
3. All existing tests pass; new convention tests stop future pages from drifting.
4. First load on mobile is visibly faster (no third-party font blocking), navigation shows
   immediate feedback, and the known slow/heavy pages are improved.

## 2. Approach

**Re-skin the foundation, then polish page by page.**

Most templates already use the shared `rd-*` components (cards, data grids, filters,
badges, tabs, nav). Re-defining tokens and those components in Pearl changes the whole
system consistently in one step, with almost no template logic touched. Each phase then
walks its pages, migrates leftovers (raw Bootstrap cards / badges / tables, inline styles)
and fixes layout/UX.

Rejected: per-page rebuild with a parallel stylesheet (long mixed-look period); new
frontend framework (risk of logic changes).

## 3. Identity tokens

Defined once in `static/css/design-system.css` (existing token blocks are replaced).
Templates and page CSS use tokens only — no raw hex colors.

| Token | Light | Dark |
|---|---|---|
| `--bs-body-bg` (page) | `#fafaf8` | `#0b0b0a` |
| `--rd-surface` (cards) | `#ffffff` | `#151514` |
| `--rd-surface-2` (wells, chips) | `#f4f4f2` | `#1f1f1d` |
| `--bs-border-color` | `#ececea` | `#2a2a27` |
| `--bs-body-color` / `--rd-ink` | `#0a0a0a` | `#f5f5f2` |
| `--rd-muted` | `#6b6b68` | `#9a9a94` |
| `--rd-faint` | `#a3a39e` | `#6b6b66` |
| `--rd-accent` (orange) | `#ff5a1f` | `#ff6a33` |
| `--rd-accent-soft` | `rgba(255,90,31,.10)` | `rgba(255,106,51,.15)` |
| `--rd-primary-bg` / `--rd-primary-fg` | `#0a0a0a` / `#fff` | `#f5f5f2` / `#0a0a0a` |
| `--rd-success` / soft | `#0a7a3e` / `#e8f6ee` | `#4ade80` / `rgba(74,222,128,.12)` |
| `--rd-warning` / soft | `#c2410c` / `#fff0e9` | `#fbbf24` / `rgba(251,191,36,.12)` |
| `--rd-danger` / soft | `#b91c1c` / `#fdecec` | `#f87171` / `rgba(248,113,113,.12)` |

Rules:

- **Primary action = ink** (black in light, near-white in dark). **Orange is the accent**:
  active state dots, focus/progress, highlights, the one "special" action per screen
  (e.g. "Charge & record sale"). Never use orange for errors.
- Existing token names consumed elsewhere (`--rd-accent`, `--rd-success`, `--bs-primary`,
  `kpi-*` classes …) keep working; their values change.
- Shadows are subtle: `0 1px 2px rgba(0,0,0,.03), 0 12px 32px -18px rgba(0,0,0,.12)`.
- Radii: cards 14–20px, inputs/buttons 11–16px, pills 999px.
- The existing global density (`html { font-size: 80% }`) stays — every page is tuned to it.

## 4. Typography

Self-hosted woff2 in `static/fonts/` (Arabic + Latin subsets only), `font-display: swap`,
`<link rel="preload">` for the two body weights. Google Fonts links are removed from all
shells.

- Body: **IBM Plex Sans Arabic** (400/500/600/700).
- Headings (page titles, card titles, brand): **Noto Kufi Arabic** (600/700).
- Numbers, money, dates, references: **Inter Tight** (500/600/700) via `.tabular-nums`
  and KPI/amount classes, with `font-variant-numeric: tabular-nums`.
- English UI uses Inter Tight for body as well.

## 5. Components (re-skinned in place, same class names)

Page header, `.rd-card` / header / body, `.rd-stat` KPI cards, data grids
(`.rd-datagrid`, `.rd-table-modern`, `.rd-mobile-row-card`), filter card partials,
pagination, buttons, `.rd-badge--*`, `.rd-section-tabs`, forms (inputs, selects,
switches), modals, toasts, sidebar, top bar, search, notifications, user menu, employee
top bar and bottom tab bar.

New shared pieces:

- KPI card variant with trend chip and optional sparkline (pure SVG/CSS, server data only).
- `partials/empty_state.html` — one empty-state pattern.
- Skeleton loading block (`.rd-skeleton`) for htmx swaps.
- iOS-style switch skin for Bootstrap `form-switch`.

Motion: 150–450 ms, transform/opacity only, `prefers-reduced-motion` disables it.
No animation blocks input.

## 6. Shells

- **Management**: Pearl sidebar (icons + grouped sections, active item = white pill with
  orange icon in light / raised surface in dark), top bar with search pill (`/`),
  notifications, theme, language, user menu. Same links and permissions as today.
- **Employee**: compact top bar (avatar, title, wallet chip), orange hairline progress where
  a page has steps, glass bottom tab bar with the raised ink "+" button. Same tabs.
- Both: thin orange top progress bar that starts the moment a boosted navigation is
  issued, and the current content dims until the new page swaps in. (Link prefetch was
  considered and dropped: htmx 1.9 has no safe way to reuse a prefetched response, and a
  cached page could show stale balances.)

## 7. Key screens

### Login (`templates/accounts/login.html`)

Desktop ≥ 992px: two halves. Start half = form (brand mini mark, language + theme pills,
eyebrow, "Welcome back", username + password with icons, show/hide password, Caps Lock
hint, ink submit button with orange arrow, help line "forgot password? contact
management"). Other half = ink panel with orange glow, faint grid, site logo/name/tagline
from `site_branding`, headline, short copy, feature pills, two floating "live" cards
(decorative).

Mobile: ink hero with animated orb + pulsing rings + rotating one-liners, bottom sheet
that slides up; focusing a field collapses the hero and grows the sheet; floating labels;
54px fields; submit ripple + loading state + double-submit guard; empty-field shake;
server errors still arrive as toasts. Form fields, POST target, `next`, CSRF unchanged.

### Employee sale entry (`templates/sales/employee_entry.html`)

One screen, no scroll on 375×667 (approved compact mockup):

- Number + payer in one block; labels live inside the inputs; paste buttons kept;
  payer prefill hint inline.
- Payment tiles (5 per row); "On account" / "Payment to employee" toggles become small
  pills in the payment block header.
- Company tiles horizontal (icon + name), product tiles as a horizontal chip row.
- Price + eSIM + new-SIM in one row; estimate (cost · profit) in the block header;
  notes behind a small "+ note" link (opens inline/sheet).
- Sticky Save (ink) + Charge (orange) above the tab bar. Success toast slides from top.
- All element IDs, names, `data-*` hooks and htmx attributes used by the page script and
  JS files stay intact; the page script is only touched where markup moved.

## 8. Speed

Measured on a copy of the dev DB (warm cache): most pages 6–18 queries and < 50 ms.
Outliers: phone-lines report 520 ms / 204 KB; management sales list 164 KB;
inventory overview 29 queries. The rest of the felt slowness is client-side.

Work items:

1. Self-hosted, subset, preloaded fonts (removes third-party render blocking).
2. Navigation feedback: immediate top progress bar + dimmed content while loading.
3. Measure first-load (Lighthouse mobile) on login, employee entry, dashboard before/after.
   Concatenate shared CSS/JS only if it measurably helps (no new build toolchain;
   static files already have long cache + `?v=` busting).
4. Phase 5: phone-lines report (query + payload); Phase 3: sales list payload;
   Phase 4: inventory overview query count.
5. Query-count regression tests (`assertNumQueries` upper bounds) for the main pages.

## 9. Keeping future pages on-identity

- Rewrite `DESIGN.md` (and `docs/DESIGN_GUIDE.md` pointer) for Pearl.
- Update `.cursor/rules/ui-navigation-and-toasts.mdc` / add a Pearl UI rule.
- Extend `core/tests_ui_conventions.py`: no raw hex colors in templates; no Bootstrap
  `badge bg-*` / `text-bg-*`; no `table-light`; no Google Fonts links; shells load the
  self-hosted fonts.

## 10. Phases

Each phase: plan → implement → tests → screenshots (desktop, 375×667, 430×932; light
and dark) → owner review.

| Phase | Scope |
|---|---|
| 0 Foundation | tokens, fonts, components, motion, nav feedback, login, DESIGN.md, convention tests, speed baseline |
| 1 Employee (mobile) | employee shell, sale entry (one screen), recent entries, record payment, stock check, check/refresh number, sale/payment edit, my balance |
| 2 Management shell | sidebar, top bar, search, notifications, user menu, dashboard |
| 3 Operations | management sales + pending payments, customers (list/detail/statements/invoices), employees |
| 4 Suppliers & stock | companies (detail, connection, Sky/Layan reconcile), products, inventory, expenses |
| 5 Reports & settings | all reports (+ phone-lines speed), audit, system settings, branding, accounts, SMS gateway, phone-refresh settings/reports |

## 11. Testing

- Full test suite green after every phase (`manage.py test`).
- New convention tests (section 9) and query-count tests (section 8).
- Browser screenshots per phase at the three viewports, both themes; RTL (Arabic) primary,
  spot-check English LTR.

## 12. Risks

- Page scripts depend on IDs/classes: markup moves keep every hook; grep before renaming.
- Dark mode contrast: check text/background pairs ≥ 4.5:1 for body text.
- Global density (80%) interacts with new sizes: tune per component, not per page.
- Font download needs network access at implementation time (one-off, files committed).
