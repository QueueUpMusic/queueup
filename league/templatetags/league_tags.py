from django import template
from league.services.achievements import prestige_badges

register = template.Library()

@register.inclusion_tag('league/partials/name_badges.html')
def name_badges(user_obj):
    return {'badges': prestige_badges(user_obj)}


@register.filter
def dict_get(mapping, key):
    """Return a dictionary value for a template key, or an empty string."""
    if not mapping:
        return ''
    return mapping.get(key, '')
