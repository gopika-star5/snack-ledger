from django import template
from sales.utils import format_inr, format_inr_rupees, format_qty, abs_qty

register = template.Library()

@register.filter(name='format_inr')
def format_inr_filter(value):
    return format_inr(value)

@register.filter(name='format_inr_rupees')
def format_inr_rupees_filter(value):
    return format_inr_rupees(value)

@register.filter(name='format_qty')
def format_qty_filter(value):
    return format_qty(value)

@register.filter(name='qty')
def qty_filter(value):
    return format_qty(value)

@register.filter(name='abs_qty')
def abs_qty_filter(value):
    return abs_qty(value)
