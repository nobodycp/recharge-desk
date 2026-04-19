# Vendored front-end assets

These files were originally loaded from `cdn.jsdelivr.net`. They are
checked in so the app:

* keeps working when an upstream CDN is blocked or rate-limited
  (this happened in Apr 2026 when CSP changes silently broke the login
  page until we tightened the allowlist),
* loads from the same origin Django serves, so we can keep CSP at
  `default-src 'self'` without per-CDN exceptions, and
* is reproducible: a deployment can be replayed offline.

## Pinned versions

| File                  | Upstream                                                             |
|-----------------------|----------------------------------------------------------------------|
| `bootstrap.min.css`   | `https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css` |
| `bootstrap.rtl.min.css` | `https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.rtl.min.css` |
| `htmx.min.js`         | `https://cdn.jsdelivr.net/npm/htmx.org@1.9.12/dist/htmx.min.js`      |

> Alpine.js was removed in Apr 2026: it was only used for the mobile
> nav toggle, but its default build needed `'unsafe-eval'` in CSP.
> See `static/js/nav-toggle.js` for the vanilla replacement.

## Refreshing

```bash
cd static/vendor
curl -fsSL -O https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css
curl -fsSL -O https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.rtl.min.css
curl -fsSL -O https://cdn.jsdelivr.net/npm/htmx.org@1.9.12/dist/htmx.min.js
```

Then bump the version numbers above, run the test suite, and commit
all three files together with the version bump in the same change.
