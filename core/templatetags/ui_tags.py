from django import template
from django.utils.translation import gettext

register = template.Library()


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
_FILTER_NOISE_KEYS = {"page", "sort", "order"}


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
