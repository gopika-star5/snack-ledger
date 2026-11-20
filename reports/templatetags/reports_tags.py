from django import template
from sales.utils import format_inr

register = template.Library()

@register.filter(name='format_inr')
def format_inr_filter(value):
    return format_inr(value)

@register.filter(name='rupees')
def rupees_filter(value):
    if value is None:
        return "0.00"
    try:
        return f"{int(value) / 100:.2f}"
    except (ValueError, TypeError):
        return "0.00"
