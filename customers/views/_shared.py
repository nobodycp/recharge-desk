"""Helpers shared between the customers view modules."""

from __future__ import annotations

from django.contrib import messages


def flash_form_errors(request, form) -> None:
    """Surface form errors as translated flash messages.

    Uses the form field's translated label (``form[field].label``) instead
    of the raw machine name, so an Arabic UI sees Arabic prefixes
    ("المبلغ:") not English snake-case ones ("amount:").
    """
    for field, errs in form.errors.items():
        if field == "__all__":
            label = ""
        else:
            try:
                label = form[field].label or field
            except KeyError:
                label = field
        prefix = f"{label}: " if label else ""
        for e in errs:
            messages.error(request, f"{prefix}{e}")
