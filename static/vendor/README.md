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
| `alpine.min.js`       | `https://cdn.jsdelivr.net/npm/alpinejs@3.14.3/dist/cdn.min.js`       |

## Refreshing

```bash
cd static/vendor
curl -fsSL -O https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css
curl -fsSL -O https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.rtl.min.css
curl -fsSL -O https://cdn.jsdelivr.net/npm/htmx.org@1.9.12/dist/htmx.min.js
curl -fsSL -o alpine.min.js https://cdn.jsdelivr.net/npm/alpinejs@3.14.3/dist/cdn.min.js
```

Then bump the version numbers above, run the test suite, and commit
all four files together with the version bump in the same change.
