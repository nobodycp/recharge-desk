# Pearl Phase 0 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the whole system to the Pearl identity (tokens, fonts, shared components, shells' colors, nav feedback, login page) and lock it in with convention + query-budget tests, without changing any view logic.

**Architecture:** Re-skin in place. Tokens and the shared `rd-*` components in `static/css/design-system.css` are redefined, so every page changes at once; page-level layout work happens in later phases. Fonts are self-hosted. The login page is rebuilt (markup + `login.css` + `login.js`) around the unchanged `BilingualLoginView` form.

**Tech Stack:** Django 4.2 templates, Bootstrap 5 (vendored, LTR + RTL), htmx 1.9, vanilla JS, plain CSS (no build step).

**Spec:** `docs/superpowers/specs/2026-10-14-pearl-redesign-design.md`

## Global Constraints

- No changes to views, forms, URLs, models, permissions, htmx endpoints or form field names.
- Tokens (light / dark): page `#fafaf8`/`#0b0b0a`; surface `#ffffff`/`#151514`; surface-2 `#f4f4f2`/`#1f1f1d`; border `#ececea`/`#2a2a27`; ink `#0a0a0a`/`#f5f5f2`; muted `#6b6b68`/`#9a9a94`; faint `#a3a39e`/`#6b6b66`; accent `#ff5a1f`/`#ff6a33`; accent-soft `rgba(255,90,31,.10)`/`rgba(255,106,51,.15)`; primary bg/fg `#0a0a0a`/`#fff` and `#f5f5f2`/`#0a0a0a`; success `#0a7a3e`/`#4ade80` (soft `#e8f6ee`/`rgba(74,222,128,.12)`); warning `#c2410c`/`#fbbf24` (soft `#fff0e9`/`rgba(251,191,36,.12)`); danger `#b91c1c`/`#f87171` (soft `#fdecec`/`rgba(248,113,113,.12)`).
- Primary action = ink; orange = accent only (active dots, focus, progress, one special action). Never orange for errors.
- Fonts: IBM Plex Sans Arabic 400/500/600/700 (body), Noto Kufi Arabic 600/700 (headings), Inter Tight 500/600/700 (numbers; also English body). Arabic + Latin subsets only, woff2, `font-display: swap`.
- Keep `html { font-size: 80% }`.
- Motion 150–450 ms, transform/opacity only; `prefers-reduced-motion` already neutralises animations globally (design-system.css:131).
- Public refresh page (`phone_refresh/templates/phone_refresh/public_refresh.html`) is out of scope — do not restyle it, exempt it from new tests.
- UI strings via `{% trans %}`; add Arabic to `locale/ar/LC_MESSAGES/django.po` and run `compilemessages` (the `.mo` is committed).
- Run the suite with `.venv/bin/python manage.py test` (settings default to development).

## Review Focus

1. **Page scripts losing hooks** — any renamed class/ID breaks JS silently. Phase 0 renames nothing in templates except `login.html`; login tests pin every form hook (`name=username|password|next`, CSRF, action).
2. **Dark-mode leftovers** — a hard-coded light color in a component shows a white box in dark mode. Pinned by `test_no_hex_outside_token_blocks` (Task 3).
3. **Login with errors** — wrong password must still re-render with a toast and keep the typed username. Pinned in Task 5 (`test_invalid_login_rerenders_with_username`).
4. **Login `next`** — `?next=/x/` must survive the redesign. Pinned in Task 5 (`test_next_is_preserved`).
5. **Branding logo present vs absent** — brand panel must show the uploaded logo or the letter mark. Pinned in Task 5 (`test_brand_panel_uses_logo_when_set`).

---

### Task 0: Speed + visual baseline

**Files:**
- Create (gitignored, not committed): `.superpowers/probe_settings.py`, `.superpowers/probe.sqlite3`, `.superpowers/make_probe_user.py`

- [ ] **Step 1:** Create `.superpowers/probe_settings.py`: `from config.settings.development import *` then `DATABASES["default"]["NAME"] = BASE_DIR / ".superpowers" / "probe.sqlite3"`; copy `db.sqlite3` → `.superpowers/probe.sqlite3`; run `PYTHONPATH=.superpowers DJANGO_SETTINGS_MODULE=probe_settings .venv/bin/python manage.py migrate`.
- [ ] **Step 2:** `make_probe_user.py` sets password `pearl-probe-1` on one MANAGEMENT user and one EMPLOYEE user in the probe DB (print usernames).
- [ ] **Step 3:** Start `runserver 8765` with the probe settings (background). In the browser tool, for `/ar/accounts/login/`, `/ar/employee/sales/` (employee), `/ar/management/` (management), at 375×667 with CPU/network throttling off, record via `Runtime.evaluate` of `performance.getEntriesByType('navigation')[0]` and `performance.getEntriesByType('paint')`: `domContentLoadedEventEnd`, `loadEventEnd`, `first-contentful-paint`, and count of `performance.getEntriesByType('resource')` + transferSize sum. Save screenshots (light + dark) of these three pages.
- [ ] **Step 4:** Write the numbers into the "Baseline" table at the end of this plan (edit the file). Commit the plan edit: `git commit -m "docs: Pearl phase 0 baseline numbers"`.

### Task 1: Self-hosted fonts

**Files:**
- Create: `tools/fetch_fonts.py`, `static/fonts/*.woff2`, `static/css/fonts.css`, `templates/partials/fonts_head.html`
- Modify: `templates/base_management.html:10-12`, `templates/base_employee.html:10-12`, `templates/accounts/login.html:10`, `templates/core/forbidden.html` (its Google Fonts `<link>`), `core/tests_ui_conventions.py`

**Interfaces:**
- Produces: `{% include "partials/fonts_head.html" %}` (preloads + stylesheet link); font family names `"IBM Plex Sans Arabic"`, `"Noto Kufi Arabic"`, `"Inter Tight"` available to CSS.

- [ ] **Step 1: Failing tests** in `core/tests_ui_conventions.py`:

```python
FONT_PAGES = SHELLS | {"templates/accounts/login.html", "templates/core/forbidden.html"}
OUT_OF_SCOPE = {"phone_refresh/templates/phone_refresh/public_refresh.html"}

def test_no_google_fonts(self):
    offenders = [n for n, t in _template_files()
                 if "fonts.googleapis.com" in t and n not in OUT_OF_SCOPE]
    self._assert_none(offenders, "Fonts are self-hosted; include partials/fonts_head.html instead:")

def test_pages_load_self_hosted_fonts(self):
    files = dict(_template_files())
    for name in FONT_PAGES:
        self.assertIn('include "partials/fonts_head.html"', files[name], name)

def test_font_faces_point_at_shipped_files(self):
    css = (BASE_DIR / "static/css/fonts.css").read_text(encoding="utf-8")
    urls = re.findall(r"url\(\"?\.\./fonts/([^\")]+)\"?\)", css)
    self.assertTrue(urls)
    for u in urls:
        self.assertTrue((BASE_DIR / "static/fonts" / u).is_file(), u)
```

- [ ] **Step 2:** Run `.venv/bin/python manage.py test core.tests_ui_conventions` → the three new tests FAIL.
- [ ] **Step 3:** `tools/fetch_fonts.py`: requests `https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&family=Noto+Kufi+Arabic:wght@600;700&family=Inter+Tight:wght@500;600;700&display=swap` with a desktop Chrome `User-Agent`, keeps only `@font-face` blocks whose preceding `/* subset */` comment is `arabic` or `latin`, downloads each woff2 to `static/fonts/<family-slug>-<weight>-<subset>.woff2` (dedupe identical URLs — variable fonts serve one file for several weights), and writes `static/css/fonts.css` with the same `@font-face` blocks (`src: url("../fonts/<file>") format("woff2")`, original `unicode-range`, `font-display: swap`). Run it (needs network).
- [ ] **Step 4:** `partials/fonts_head.html`: `<link rel="preload" as="font" type="font/woff2" crossorigin>` for IBM Plex Sans Arabic 400 + 600 arabic subset files, then `<link href="{% static 'css/fonts.css' %}{{ static_cache_query }}" rel="stylesheet">`. Replace the preconnect + Google `<link>` lines in the four pages with the include (placed before `{{ bootstrap_css }}`).
- [ ] **Step 5:** Tests pass; full `core` tests pass.
- [ ] **Step 6:** Commit `feat(ui): self-hosted Pearl fonts`.

### Task 2: Pearl tokens and typography

**Files:**
- Modify: `static/css/design-system.css:21-188` (root tokens, light block, dark block, typography)
- Create: `core/tests_design_tokens.py`

**Interfaces:**
- Produces CSS custom properties used by all later tasks: `--rd-ink`, `--rd-muted`, `--rd-faint`, `--rd-surface`, `--rd-surface-2`, `--rd-accent`, `--rd-accent-soft`, `--rd-accent-ink` (text-safe orange: light `#c2410c`, dark `#ff8a5c`), `--rd-primary-bg`, `--rd-primary-fg`, `--rd-success|warning|danger` + `-soft`, `--rd-font`, `--rd-font-ar`, `--rd-font-heading`, `--rd-font-num`, `--rd-shadow-card`. Existing names keep working with new values: `--bs-body-bg/-color`, `--bs-secondary-bg` (= surface), `--bs-tertiary-bg` (= surface-2), `--bs-border-color`, `--bs-primary` + `-rgb` (= ink), `--bs-link-color` (= ink), `--bs-link-hover-color` (= accent-ink), `--rd-accent-hover`, `--rd-accent-muted` (= accent-soft), `--rd-accent-2*` (remapped to the orange family; `--rd-accent-2-strong` = `#c2410c` light / `#ff8a5c` dark), `--rd-info`/`-soft` (= ink / surface-2).

- [ ] **Step 1: Failing test** `core/tests_design_tokens.py`:

```python
def _block(css, selector):  # text between `selector {` and its matching `}`
    ...
class PearlTokenTests(SimpleTestCase):
    def test_light_tokens(self):
        b = _block(CSS, '[data-bs-theme="light"]')
        for k, v in {"--bs-body-bg": "#fafaf8", "--rd-ink": "#0a0a0a", "--rd-accent": "#ff5a1f",
                     "--bs-border-color": "#ececea", "--rd-primary-bg": "#0a0a0a"}.items():
            self.assertRegex(b, rf"{re.escape(k)}:\s*{v}\b", k)
    def test_dark_tokens(self):
        b = _block(CSS, '[data-bs-theme="dark"]')
        for k, v in {"--bs-body-bg": "#0b0b0a", "--rd-ink": "#f5f5f2", "--rd-accent": "#ff6a33",
                     "--rd-surface": "#151514", "--rd-primary-bg": "#f5f5f2"}.items():
            self.assertRegex(b, rf"{re.escape(k)}:\s*{v}\b", k)
    def test_fonts(self):
        root = _block(CSS, ":root")
        self.assertIn('"IBM Plex Sans Arabic"', root)
        self.assertIn('"Noto Kufi Arabic"', root)
        self.assertIn('"Inter Tight"', root)
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Rewrite the token blocks with the Global Constraints values; `--rd-font-ar: "IBM Plex Sans Arabic", "Inter Tight", system-ui, sans-serif`; `--rd-font: "Inter Tight", system-ui, sans-serif`; `--rd-font-heading: "Noto Kufi Arabic", var(--rd-font-ar)`; `--rd-font-num: "Inter Tight", system-ui, sans-serif`. Typography section: `.rd-heading-xl`, `h1–h4`, `.rd-card-header` titles use `--rd-font-heading` in Arabic (`html[lang="ar"]`); `.tabular-nums`, `.rd-stat-value`, `kpi-*` use `--rd-font-num`; drop `letter-spacing: -0.01em` for Arabic body (Arabic must not be tracked).
- [ ] **Step 4:** Tests pass; commit `feat(ui): Pearl tokens and typography`.

### Task 3: Re-skin shared components and chrome

**Files:**
- Modify: `static/css/design-system.css` (sections from line 189 to end), `static/css/app.css`, `static/css/number-check.css`
- Modify: `core/tests_design_tokens.py`

**Interfaces:**
- Consumes: Task 2 tokens.
- Produces new classes: `.btn-accent` (orange fill, white text), `.rd-trend` + `.rd-trend--up|--down` (pill chip), `.rd-skeleton` (shimmer block), `.form-switch` Pearl skin (track `--rd-line` → `--rd-accent` when checked, 2.25rem×1.3rem).

- [ ] **Step 1: Failing test** (append to `core/tests_design_tokens.py`):

```python
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
def test_no_hex_outside_token_blocks(self):
    body = CSS.split("/* @end-tokens */", 1)[1]   # marker placed right after the dark block
    offenders = [l for l in body.splitlines() if HEX.search(l)]
    self.assertEqual(offenders, [], "Use Pearl tokens, not raw hex, below the token blocks")
def test_page_css_has_no_hex(self):
    for f in ("app.css", "number-check.css"):
        text = (BASE_DIR / "static/css" / f).read_text(encoding="utf-8")
        self.assertEqual([l for l in text.splitlines() if HEX.search(l)], [], f)
```

- [ ] **Step 2:** Run → FAIL (≈130 + 44 hex lines today).
- [ ] **Step 3: Core components.** Page header, cards (`--rd-surface`, 1px `--bs-border-color`, `--rd-shadow-card`, radius 1.125rem), `.rd-stat` (icon chip on `--rd-surface-2`, value in `--rd-font-num`), buttons (`.btn-primary` = `--rd-primary-bg/fg`, hover lift 1px; `.btn-outline-secondary` = surface + border; focus ring `0 0 0 .25rem` of ink 7%), forms (inputs radius .75rem, focus border ink + soft ring; `.form-switch` skin), `.rd-badge--paid|pending|cancelled|neutral` → success/warning/danger/surface-2 soft pills with a leading 6px dot, `.rd-section-tabs` (active = ink text + 2px orange underline), tables/datagrid (header text `--rd-muted`, row hover `--rd-surface-2`, hairline rows), filter cards, pagination (active page ink pill), `.empty-state`, `.rd-skeleton`, `.rd-trend`.
- [ ] **Step 4: Chrome.** Sidebar: replace the lilac palette (lines 430–820) — background `--rd-surface` (light) / `#151514` token (dark), item text `--rd-muted`, hover `--rd-surface-2`, active item = surface pill with 1px border + ink text + orange icon (light) / `--rd-surface-2` + ink text + orange icon (dark); count chips stay red via `--rd-danger`. Topbar: translucent page color + blur, search pill, bell, user menu on Pearl tokens. Toasts: ink background, success/danger icon dot. Employee top bar + bottom tab bar: replace the teal "Sales" bubble (lines 2062–2270) with an ink FAB + orange dot, glass bar on `--rd-surface` 85%. Nav progress (`.rd-nav-progress`, line 3487): 2px `--rd-accent` bar.
- [ ] **Step 5: Page sections + legacy.** Convert every remaining hex in design-system.css (dashboard charts, profit report, phone lifecycle donut, invoice, inventory, Sky reconcile, rank chips, avatars), `app.css` and `number-check.css` to tokens. Charts/donuts: series colors = ink, accent, `--rd-faint`, `--rd-line`; success/danger semantics kept. Rank chips: 1st = ink, 2nd = accent, 3rd = faint. Legacy `.card` / `.app-card` / `.table-light` compat rules map to the same Pearl values. Insert the `/* @end-tokens */` marker after the dark block.
- [ ] **Step 6:** Tests pass; full suite green.
- [ ] **Step 7: Visual check** on the probe server: dashboard, sales list, customer detail, inventory overview, system settings, employee entry, employee recent — 1280×800 and 375×667, light + dark. Fix contrast issues (body text ≥ 4.5:1). Screenshot set kept for the phase report.
- [ ] **Step 8:** Commit `feat(ui): Pearl re-skin of shared components and shells`.

### Task 4: Immediate navigation feedback

**Files:**
- Modify: `static/js/nav.js:144-156, ~203`, `static/css/design-system.css` (`.rd-nav-progress`, `[data-rd-live].is-live-loading`)

- [ ] **Step 1:** In `startProgress()` remove the 120 ms delay (activate synchronously) and add `is-live-loading` to `[data-rd-live]` at the same moment for body-targeted boosted requests (it is currently added later at ~line 203 — move it into the same code path; `stopProgress()` already clears it).
- [ ] **Step 2:** CSS: `.rd-nav-progress.is-active` animates width 0→80% over 2.5s ease-out, completes to 100% + fades on stop; `[data-rd-live].is-live-loading { opacity: .55; transition: opacity .15s; pointer-events: none; }`. The employee shell's main region (`.rd-employee-main`) gets `data-rd-live` too (`templates/base_employee.html:58`).
- [ ] **Step 3:** Manual check on the probe server: click between dashboard → sales → customers; bar appears instantly, content dims, no stuck dim after a 4xx/5xx (`stopProgress` runs on `htmx:afterRequest` / `htmx:sendError` — verify the listeners exist; add `htmx:responseError` if missing).
- [ ] **Step 4:** `core` tests green; commit `feat(nav): instant progress and dimmed content while loading`.

### Task 5: Login page

**Files:**
- Modify: `templates/accounts/login.html` (rewrite), `accounts/forms.py:15-21` (widget attrs only), `locale/ar/LC_MESSAGES/django.po` (+ `.mo`)
- Create: `static/css/login.css`, `static/js/login.js`
- Modify: `accounts/tests.py` (new `LoginPageTests`), `static/css/design-system.css` (delete old "Login alignment" section, lines 1871–1923)

**Interfaces:**
- Consumes: Task 1 fonts partial, Task 2 tokens, toast region + `toast.js` (unchanged).
- Produces DOM hooks used by `login.js`: `form[data-rd-login]`, `[data-rd-password-toggle]` (button, `aria-pressed`, labels from `data-show-label` / `data-hide-label`), `[data-rd-caps-hint]` (hidden by default), `.rd-login-field` wrappers (get `.is-focused`, `.is-filled`, `.is-invalid`), `body.rd-login.is-typing` (mobile hero collapse), submit `button[data-rd-login-submit]` (gets `.is-loading`, `disabled`).

- [ ] **Step 1: Failing tests** `accounts/tests.py`:

```python
class LoginPageTests(TestCase):
    url = reverse_lazy("accounts:login")
    def test_form_hooks_present(self):
        r = self.client.get(self.url)
        for s in ('name="username"', 'name="password"', 'name="next"', "csrfmiddlewaretoken",
                  "data-rd-password-toggle", "data-rd-caps-hint", "js/login.js", "css/login.css"):
            self.assertContains(r, s)
    def test_next_is_preserved(self):
        r = self.client.get(f"{self.url}?next=/ar/management/customers/")
        self.assertContains(r, 'value="/ar/management/customers/"')
    def test_invalid_login_rerenders_with_username(self):
        User.objects.create_user("pearl", password="right-pass-123")
        r = self.client.post(self.url, {"username": "pearl", "password": "wrong"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="pearl"')
        self.assertContains(r, "data-rd-flash")          # becomes a toast
    def test_valid_login_redirects(self):
        User.objects.create_user("pearl", password="right-pass-123")
        r = self.client.post(self.url, {"username": "pearl", "password": "right-pass-123"})
        self.assertEqual(r.status_code, 302)
    def test_brand_panel_uses_logo_when_set(self):
        # no logo -> letter mark; with SiteBranding.logo -> <img class="rd-login-logo">
        ...
```

(`test_brand_panel_uses_logo_when_set`: assert `rd-login-mark` without a logo; then save a `SiteBranding` with a small in-memory PNG `SimpleUploadedFile` under `override_settings(MEDIA_ROOT=tmpdir)` and assert `rd-login-logo`.)

- [ ] **Step 2:** Run `manage.py test accounts.tests.LoginPageTests` → FAIL.
- [ ] **Step 3: Markup** — keep `{% include "partials/toast_region.html" %}`, `form_errors_toast` include and the `data-rd-flash` block exactly as today. Structure: `body.rd-login` > `.rd-login-split` > `section.rd-login-form-side` (top row: mini mark + `site_name`, theme toggle partial + language form unchanged; eyebrow, `h1`, lead, form, help line, footer) + `aside.rd-login-brand` (`aria-hidden="true"` for decorative parts: glow, grid, floating cards; real content: logo or `rd-login-mark` letter `{{ site_name|slice:":1" }}`, `site_name`, `site_tagline`, headline, copy, three feature pills). Mobile hero (`.rd-login-hero`, orb + rings + rotating lines) is the same `aside` restyled under 992px. Field markup per field: `.rd-login-field` > icon svg + `<label>` + `{{ form.field }}`; password field adds the toggle button and caps hint.
- [ ] **Step 4: Copy** (English msgid → Arabic): "Management & sales desk" → "لوحة الإدارة والمبيعات"; "Welcome back" → "أهلاً بعودتك"; "Sign in and pick up where you left off." → "سجّل دخولك وكمّل شغلك من حيث وقفت."; "Forgot your password? Ask management to reset it." → "نسيت كلمة المرور؟ تواصل مع الإدارة لإعادة تعيينها."; "Every recharge," / "in one place." → "كل عمليات الشحن،" / "بمكان واحد."; "Live sales, company and customer balances, and SIM stock — accurate and fast, from desktop or phone." → "مبيعات لحظية، أرصدة الشركات والعملاء، ومخزون الشرائح — بدقة وسرعة، من الكمبيوتر أو الجوال."; pills "Live sales" / "Accurate balances" / "Clear reports" → "مبيعات لحظية" / "أرصدة دقيقة" / "تقارير واضحة"; ticker "Sell from your phone in seconds" / "Company balances always up to date" / "Every operation saved and reviewed" → "مبيعات لحظية من جوالك" / "أرصدة الشركات محدّثة دائماً" / "كل عملية محفوظة ومراجَعة"; "Show" / "Hide" → "إظهار" / "إخفاء"; "Caps Lock is on" → "زر الأحرف الكبيرة (Caps Lock) مفعّل"; "Secure connection" → "اتصال آمن". Existing msgids ("Sign in", "Username", "Password") reused. `compilemessages -l ar`.
- [ ] **Step 5: `login.css`** — desktop ≥ 992px: two equal columns, form side on the inline-start, brand panel ink with orange radial glow + masked grid + two floating cards (`fcin` then `bob` animations). < 992px: hero 42vh ink with orb/rings/ticker, form side becomes a bottom sheet (radius 30px top, slide-up 0.8s), fields 54px (3.375rem at 80% root → use `3.4rem`), floating labels (label sits inside, moves up on `.is-focused/.is-filled`), `body.is-typing` shrinks hero to 18vh and hides headline/ticker. Uses tokens only (no hex) — add `login.css` to `test_page_css_has_no_hex`.
- [ ] **Step 6: `login.js`** (IIFE, no globals): focus/blur → `.is-focused`/`.is-filled` + `body.is-typing` (with 120 ms blur grace); toggle password type + swap label + `aria-pressed`; `keydown/keyup` → `getModifierState("CapsLock")` shows hint; submit → if a field is empty: `preventDefault`, add `.is-invalid` (CSS shake), focus first empty; else add `.is-loading` + disable submit (double-submit guard); `pageshow` with `persisted` clears loading state.
- [ ] **Step 7:** `accounts/forms.py`: username widget attrs `autocomplete="username"`, `autocapitalize="none"`, `spellcheck="false"`, `class="rd-login-input"`; password `autocomplete="current-password"`, `class="rd-login-input"`. Placeholder `" "` on both (needed for `:placeholder-shown` fallback).
- [ ] **Step 8:** Tests pass (`accounts` + `core`).
- [ ] **Step 9: Visual check** 1280×800, 375×667, 430×932, light + dark, Arabic + English; error state (wrong password) shows toast + kept username.
- [ ] **Step 10:** Commit `feat(accounts): Pearl login page`.

### Task 6: Identity guardrails for future pages

**Files:**
- Modify: `core/tests_ui_conventions.py`, `DESIGN.md` (rewrite), `docs/DESIGN_GUIDE.md` (pointer to DESIGN.md), `.cursor/rules/ui-navigation-and-toasts.mdc` (link to the new rule)
- Create: `.cursor/rules/pearl-ui.mdc`

- [ ] **Step 1: Failing tests** with shrink-only legacy lists (files that still need migration in later phases; the list is computed once now and may only get shorter):

```python
RAW_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
BS_BADGE = re.compile(r"\bbadge\b[^\"]*\b(text-bg-|bg-)\w+")
LEGACY_BADGES = {...}       # current offenders (≈10 files)
LEGACY_TABLE_LIGHT = {...}  # current offenders
LEGACY_HEX = {"templates/sms_gateway/settings_index.html"}

def test_no_raw_hex_colors_in_templates(self): ...   # outside LEGACY_HEX / OUT_OF_SCOPE
def test_no_bootstrap_color_badges(self): ...        # use .rd-badge--*
def test_no_table_light(self): ...                   # rd-table-modern styles the header
def test_legacy_lists_only_shrink(self):
    # every file in a LEGACY_* set must still offend; otherwise remove it from the set
```

- [ ] **Step 2:** Run → new tests FAIL until the legacy sets are filled; fill them from the actual offenders; PASS.
- [ ] **Step 3:** Rewrite `DESIGN.md` for Pearl: identity + token table, fonts, primary-vs-accent rule, components (same sections as today, Pearl values), motion, mobile rules (one-screen entry, 44px+ touch targets), dark mode, "Do not" list (raw hex, Bootstrap color badges, `table-light`, Google Fonts, inline styles for repeated patterns, orange for errors). `pearl-ui.mdc` (alwaysApply) = 15-line summary pointing to DESIGN.md.
- [ ] **Step 4:** Commit `docs(ui): Pearl design system guide and convention tests`.

### Task 7: Query budgets

**Files:**
- Create: `core/tests_query_budget.py`

- [ ] **Step 1:** Test class with a fixture: 1 management user, 1 employee user (+ employee profile as the existing tests in `accounts/tests.py` build them), 2 companies with 1 product each, 1 payment method, 3 customers, 10 sales. Helper:

```python
def assert_max_queries(self, url, user, budget): ...  # force_login, warm GET, then CaptureQueriesContext GET; assert len <= budget
```

- [ ] **Step 2:** Measure each page once (print), then pin budgets = measured + 2 for: `reports:dashboard`, `sales:management_sale_list`, `customers:customer_list`, `inventory:overview`, `sales:employee_entry`, `sales:employee_recent_sales`.
- [ ] **Step 3:** Tests pass; commit `test(perf): query budgets for main pages`.

### Task 8: Phase verification

- [ ] **Step 1:** Full suite: `.venv/bin/python manage.py test` → all green.
- [ ] **Step 2:** Re-run Task 0 Step 3 measurements; fill the "After" columns below.
- [ ] **Step 3:** Screenshot set (Task 3 Step 7 pages + login) at 1280×800 / 375×667 / 430×932, light + dark, for the owner review.

## Baseline (filled by Task 0 / Task 8)

| Page | FCP before | FCP after | Requests before | Requests after | Transfer before | Transfer after |
|---|---|---|---|---|---|---|
| login | | | | | | |
| employee entry | | | | | | |
| dashboard | | | | | | |
