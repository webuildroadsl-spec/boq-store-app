from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Dict lookup by a variable key, e.g. {{ some_dict|get_item:some_key }} —
    Django's template language has no built-in way to do this."""
    if mapping is None:
        return None
    return mapping.get(key)
