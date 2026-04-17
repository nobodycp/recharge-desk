from django import template

from core.datagrid import PER_PAGE_CHOICES, windowed_page_numbers

register = template.Library()


@register.simple_tag
def dg_per_page_choices():
    return PER_PAGE_CHOICES


@register.simple_tag
def dg_windowed_pages(current, total, radius=2):
    return windowed_page_numbers(int(current), int(total), radius=radius)
