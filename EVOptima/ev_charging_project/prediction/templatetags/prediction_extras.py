from django import template
from django.template.defaultfilters import floatformat

register = template.Library()

@register.filter
def div(value, arg):
    try:
        return float(value) / float(arg)
    except (ValueError, ZeroDivisionError):
        return 0

@register.filter
def mul(value, arg):
    try:
        return float(value) * float(arg)
    except ValueError:
        return 0

@register.filter
def index(value, arg):
    try:
        return value[arg]
    except (IndexError, TypeError):
        return None

@register.filter
def get(value, arg):
    try:
        return value.get(arg)
    except (KeyError, AttributeError):
        return None
