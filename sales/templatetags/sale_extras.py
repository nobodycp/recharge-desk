from django import template

from sales.charging import sale_charge_not_sent

register = template.Library()


@register.filter
def charge_not_sent(sale):
    return sale_charge_not_sent(sale)
