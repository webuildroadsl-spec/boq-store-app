from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Dict lookup by a variable key, e.g. {{ some_dict|get_item:some_key }} —
    Django's template language has no built-in way to do this."""
    if mapping is None:
        return None
    return mapping.get(key)


@register.filter
def qty(value):
    """A BOQ quantity: at least 3 decimals, more only if it has them."""
    from boq.numbers import quantity
    return quantity(value)


@register.filter
def rate(value):
    """A BOQ rate: at least 2 decimals, more only if it has them."""
    from boq.numbers import rate as fmt
    return fmt(value)
