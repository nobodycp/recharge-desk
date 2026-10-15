from datetime import date

from django import template
from django.apps import apps
from django.core.files.storage import default_storage
from django.utils.translation import gettext, pgettext_lazy

register = template.Library()

_FAVICON_MIME = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}


@register.filter(name="image_mime_type")
def image_mime_type(url: str) -> str:
    """Guess MIME type from a file URL extension (for favicon link tags)."""
    if not url:
        return "image/png"
    lower = str(url).lower().split("?", 1)[0]
    for ext, mime in _FAVICON_MIME.items():
        if lower.endswith(ext):
            return mime
    return "image/png"


@register.simple_tag
def branding_favicon_href(branding) -> str:
    """Favicon URL with fallback to logo when favicon file is missing on disk."""
    if not branding:
        return ""
    if (
        branding.favicon
        and branding.favicon.name
        and default_storage.exists(branding.favicon.name)
    ):
        return branding.favicon.url
    if (
        branding.logo
        and branding.logo.name
        and default_storage.exists(branding.logo.name)
    ):
        return branding.logo.url
    return ""


@register.filter(name="tdb")
def translate_db_value(value):
    """
    Translate a string sourced from the database using the active locale.

    Useful for display labels that live as data (e.g. PaymentMethod.name)
    but still want a localized rendering. Strings without a registered
    translation pass through unchanged.

    Anchor msgids in `<app>/_i18n_*.py` modules so `makemessages` picks
    them up and `compilemessages` ships them in the `.mo` files.
    """
    if value is None:
        return ""
    return gettext(str(value))


@register.simple_tag(takes_context=True)
def querystring(context, **kwargs):
    """
    Build a query string from the current request GET, with overrides.
    Example: {% querystring page=2 %}  -> ?foo=bar&page=2
    """
    request = context["request"]
    q = request.GET.copy()
    for key, value in kwargs.items():
        if value is None or value == "":
            q.pop(key, None)
        else:
            q[key] = str(value)
    if not q:
        return ""
    return "?" + q.urlencode()


@register.simple_tag(takes_context=True)
def page_url(context, page_num, page_key="page"):
    """Build URL for a given page number, preserving GET and using page_key (e.g. page or ledger_page)."""
    q = context["request"].GET.copy()
    q[str(page_key)] = str(page_num)
    return "?" + q.urlencode()


# Query-string keys that never count as "the user filtered something":
# pagination + sort cursors that the page itself manages.
_FILTER_NOISE_KEYS = {"page", "sort", "order", "per_page", "threshold_days"}


@register.simple_tag(takes_context=True)
def active_filter_count(context):
    """Return how many non-empty filter params the current request carries.

    Used by collapsible filter bars to show a small badge ("3 active")
    so that even with the bar collapsed the user can tell that filters
    are narrowing the table.
    """
    request = context.get("request")
    if request is None:
        return 0
    count = 0
    for key in request.GET:
        if key in _FILTER_NOISE_KEYS:
            continue
        for value in request.GET.getlist(key):
            if value not in (None, ""):
                count += 1
                break
    return count


@register.filter(name="model_verbose")
def model_verbose(label: str) -> str:
    """``"sales.sale"`` → the model's translated verbose name, else the label."""
    try:
        return str(apps.get_model(label)._meta.verbose_name)
    except (LookupError, ValueError, TypeError):
        return label or ""


@register.filter(name="audit_changes")
def audit_changes(changes):
    """Flatten an audit ``changes`` payload into ``{field, old, new}`` rows.

    Field diffs are stored as ``{field: {"old": ..., "new": ...}}``; deletes
    keep a ``_snapshot`` dict of the final values, shown as old-only rows.
    """
    if not isinstance(changes, dict):
        return []
    rows = []
    for field, value in changes.items():
        if field == "_snapshot" and isinstance(value, dict):
            for snap_field, snap_value in value.items():
                rows.append({"field": snap_field, "old": snap_value, "new": None})
        elif isinstance(value, dict) and ("old" in value or "new" in value):
            rows.append({"field": field, "old": value.get("old"), "new": value.get("new")})
        else:
            rows.append({"field": field, "old": None, "new": value})
    return rows


def _series_values(series, key):
    """Numeric value of ``key`` for every bucket (missing / ``None`` → 0)."""
    values = []
    for item in series or []:
        try:
            values.append(float(item.get(key) or 0))
        except (AttributeError, TypeError, ValueError):
            values.append(0.0)
    return values


def _fmt_coord(n: float) -> str:
    return f"{round(n, 2):g}"


@register.simple_tag
def rd_sparkline_points(series, key: str = "count", width: int = 100, height: int = 28) -> str:
    """SVG ``points`` string for a KPI sparkline built from a dashboard series.

    x is spread evenly over ``width``; y is scaled between the series minimum and
    maximum inside ``height`` with 2 px of padding. Fewer than two buckets give
    ``""``, and so does an all-zero series (a flat line carries no information
    and would read like a progress bar); a flat non-zero series is drawn across
    the middle.
    """
    values = _series_values(series, key)
    if len(values) < 2 or not any(values):
        return ""
    pad = 2
    low, high = min(values), max(values)
    span = high - low
    usable = height - 2 * pad
    last = len(values) - 1
    points = []
    for i, value in enumerate(values):
        if span:
            y = height - pad - (value - low) / span * usable
        else:
            y = height - pad if not value else height / 2
        points.append(f"{_fmt_coord(i * width / last)},{_fmt_coord(y)}")
    return " ".join(points)


@register.simple_tag
def rd_trend(series, key: str = "count"):
    """Compare the last bucket with the previous one.

    Returns ``{"direction": "up"|"down"|"flat", "pct": int}`` where ``pct`` is the
    absolute percentage change (the direction carries the sign), or ``None`` when
    there are fewer than two buckets or the previous value is 0.
    """
    values = _series_values(series, key)
    if len(values) < 2:
        return None
    previous, current = values[-2], values[-1]
    if not previous:
        return None
    pct = int(round(abs(current - previous) / abs(previous) * 100))
    if pct == 0:
        direction = "flat"
    else:
        direction = "up" if current > previous else "down"
    return {"direction": direction, "pct": pct}


# One-letter weekday labels for the dashboard chart. Each weekday has its own
# context so English "T"/"S" (Tue/Thu, Sat/Sun) can translate independently.
# Index = ``date.weekday()`` (Monday = 0).
_WEEKDAY_INITIALS = (
    pgettext_lazy("weekday initial: Monday", "M"),
    pgettext_lazy("weekday initial: Tuesday", "T"),
    pgettext_lazy("weekday initial: Wednesday", "W"),
    pgettext_lazy("weekday initial: Thursday", "T"),
    pgettext_lazy("weekday initial: Friday", "F"),
    pgettext_lazy("weekday initial: Saturday", "S"),
    pgettext_lazy("weekday initial: Sunday", "S"),
)


@register.filter(name="rd_weekday_initial")
def rd_weekday_initial(iso_date) -> str:
    """Localised one-letter weekday for an ISO date (``"2026-10-12"`` → ``"M"`` / ``"ن"``).

    Resolved at render time so the cached chart payload stays language-neutral.
    Accepts ``date`` objects too; anything unparsable gives ``""``.
    """
    if isinstance(iso_date, date):
        day = iso_date
    else:
        try:
            day = date.fromisoformat(str(iso_date))
        except (TypeError, ValueError):
            return ""
    return str(_WEEKDAY_INITIALS[day.weekday()])


@register.filter(name="audit_has_old")
def audit_has_old(rows):
    return any(r.get("old") not in (None, "") for r in rows or [])


@register.filter(name="audit_has_new")
def audit_has_new(rows):
    return any(r.get("new") not in (None, "") for r in rows or [])
