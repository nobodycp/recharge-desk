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
