from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def page_url(context, param, number):
    """
    Build a querystring that sets ?<param>=<number> while keeping every other
    GET parameter (filters, search, other paginators) exactly as it was.
    """
    query = context['request'].GET.copy()
    query[param] = number
    return '?' + query.urlencode()


@register.simple_tag
def page_window(page_obj, radius=2):
    """Page numbers to show: first, last, and `radius` either side of current."""
    total = page_obj.paginator.num_pages
    current = page_obj.number
    keep = {1, total}
    keep.update(range(max(1, current - radius), min(total, current + radius) + 1))
    out, prev = [], 0
    for n in sorted(keep):
        if n - prev > 1:
            out.append(None)          # None renders as an ellipsis
        out.append(n)
        prev = n
    return out


@register.simple_tag(takes_context=True)
def sort_url(context, key, current, direction, default_dir='desc'):
    """Querystring for a sortable column header: same column flips direction, a new one uses `default_dir`."""
    query = context['request'].GET.copy()
    query['sort'] = key
    query['dir'] = ('asc' if direction == 'desc' else 'desc') if key == current else default_dir
    query.pop('page', None)
    return '?' + query.urlencode()


@register.filter
def get_item(mapping, key):
    """{{ dict|get_item:key }} — look something up by a variable key."""
    try:
        return mapping.get(key, '')
    except AttributeError:
        return ''
