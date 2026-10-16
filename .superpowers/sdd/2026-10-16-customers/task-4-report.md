# Task 4 report — Statement + detailed invoice

## Status

Complete.

## Implementation

- Migrated the customer statement and detailed invoice screen headers to `rd_page_header`.
- Kept print-only behavior, letterhead/summary/signature includes, period GET parameters and ids, CSV/cross-links, and `rd-print-flow`.
- Removed all `table-light` usage and applied `rd-table-modern` to document tables.
- Removed both templates from `LEGACY_TABLE_LIGHT` and `LEGACY_PAGE_HEAD`.

## TDD

- Added response tests for the kit header, modern table marker, and absence of `table-light`.
- Red: both tests failed because the old templates did not render `<header class="rd-page-head">`.
- Green: the focused document tests and UI convention tests passed after implementation.

## Verification

- `/Users/nobody1/Desktop/prosim/account_manger/.venv/bin/python manage.py test customers core`
- Result: 459 tests passed.
- IDE lint diagnostics: no errors in changed files.
- `git diff --check`: passed.

## Commit

- `b199f9c feat(customers): statement and invoice on the page kit`

## Self-review

No blocking findings. Verified the preserved period ids/names, actions and cross-links, print controls, document partials, and legacy-set cleanup.

## Concerns

The test run emits existing environment warnings for LibreSSL, the absent worktree `staticfiles/` directory, and database setting overrides; they do not fail the suite.
