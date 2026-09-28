from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Dict lookup by a variable key — same small helper as
    boq.templatetags.boq_extras.get_item, kept as its own copy here
    rather than a cross-app import so the store app doesn't depend on
    boq's template tags for something this trivial."""
    if mapping is None:
        return None
    return mapping.get(key)
