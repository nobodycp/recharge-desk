from django import template

from sales.charging import sale_charge_not_sent

register = template.Library()


@register.filter
def charge_not_sent(sale):
    return sale_charge_not_sent(sale)


@register.filter
def charge_failure_reason(message):
    """The cause after the service prefix, for the failed-charge chip."""
    text = (message or "").strip()
    mark = " — "
    if mark in text:
        return text.split(mark, 1)[1].strip()
    return text
