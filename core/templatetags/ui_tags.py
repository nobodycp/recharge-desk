from django import template

register = template.Library()


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
