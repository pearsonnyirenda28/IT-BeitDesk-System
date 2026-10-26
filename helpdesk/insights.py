"""
Reporting queries for the two admin dashboards:

* staff_performance()  — how each IT staff member is doing
* demand()             — who / which office / department / section raises more or fewer requests

Pure query logic (no request/response handling) so the numbers can be tested directly.
All ticket aggregation is done on the ticket database; people are looked up by id in
Python, because users live on the default database while tickets may live on a per-year one.
"""
from datetime import date, datetime, time, timedelta

from django.contrib.auth.models import User
from django.db.models import Avg, Count, Max, Q
from django.utils import timezone

from .models import Ticket, TicketApproval, TicketComment

OPEN_STATES = ['OPEN', 'IN_PROGRESS', 'PENDING', 'REOPENED']
DONE_STATES = ['RESOLVED', 'CLOSED']
NOT_SET = '(not set)'
GROUP_FIELDS = {'office': 'requester_office', 'department': 'requester_department',
                'section': 'requester_section'}
PERIOD_CHOICES = [('7', 'Last 7 days'), ('30', 'Last 30 days'), ('90', 'Last 90 days'),
                  ('365', 'Last 12 months'), ('all', 'All time'), ('custom', 'Custom range')]


# ── Periods ───────────────────────────────────────────────────────────────────

def parse_period(params, now=None):
    """Read ?period= (and ?from= / ?to= for 'custom') into a dict the queries understand."""
    now = now or timezone.now()
    key = params.get('period', '30')
    if key == 'custom':
        try:
            d1 = date.fromisoformat(params.get('from', ''))
            d2 = date.fromisoformat(params.get('to', ''))
            if d2 < d1:
                d1, d2 = d2, d1
            tz = timezone.get_current_timezone()
            return {'key': 'custom', 'start': timezone.make_aware(datetime.combine(d1, time.min), tz),
                    'end': timezone.make_aware(datetime.combine(d2, time.max), tz),
                    'label': f'{d1:%d %b %Y} – {d2:%d %b %Y}',
                    'from': d1.isoformat(), 'to': d2.isoformat()}
        except ValueError:
            key = '30'
    if key == 'all':
        return {'key': 'all', 'start': None, 'end': None, 'label': 'All time', 'from': '', 'to': ''}
    if key not in ('7', '30', '90', '365'):
        key = '30'
    return {'key': key, 'start': now - timedelta(days=int(key)), 'end': now,
            'label': dict(PERIOD_CHOICES)[key], 'from': '', 'to': ''}


def in_period(qs, field, period):
    if period['start']:
        qs = qs.filter(**{f'{field}__gte': period['start']})
    if period['end']:
        qs = qs.filter(**{f'{field}__lte': period['end']})
    return qs


def fmt_minutes(m):
    """1 → '1m', 135 → '2h 15m', 3000 → '2d 2h'; None → '—'."""
    if m is None:
        return '—'
    m = int(round(m))
    if m < 60:
        return f'{m}m'
    if m < 1440:
        return f'{m // 60}h {m % 60}m'
    return f'{m // 1440}d {(m % 1440) // 60}h'


def _pct(part, whole):
    return round(100 * part / whole) if whole else None


# ── Staff performance ─────────────────────────────────────────────────────────

def staff_performance(period):
    """
    One row per active IT staff member. The cohort is the tickets *created* in the
    period that are assigned to them (so every figure in a row refers to the same
    set of tickets); 'open now' is their live workload regardless of period.
    """
    tickets = in_period(Ticket.objects.all(), 'created_at', period)
    per = {r['assigned_to']: r for r in tickets.exclude(assigned_to=None).values('assigned_to').annotate(
        total=Count('id'),
        resolved=Count('id', filter=Q(status__in=DONE_STATES)),
        breached=Count('id', filter=Q(sla_breach=True)),
        urgent=Count('id', filter=Q(priority__in=['HIGH', 'CRITICAL'])),
        avg_min=Avg('resolution_time_minutes', filter=Q(status__in=DONE_STATES)),
    )}
    open_now = {r['assigned_to']: r['n'] for r in Ticket.objects.filter(status__in=OPEN_STATES)
                .exclude(assigned_to=None).values('assigned_to').annotate(n=Count('id'))}
    comments = {r['author']: r['n'] for r in in_period(TicketComment.objects.exclude(author=None), 'created_at', period)
                .values('author').annotate(n=Count('id'))}
    signoffs = {r['signer']: r['n'] for r in in_period(TicketApproval.objects.exclude(signer=None), 'signed_at', period)
                .values('signer').annotate(n=Count('id'))}

    rows = []
    for u in User.objects.filter(is_staff=True, is_active=True).order_by('first_name', 'username'):
        r = per.get(u.pk, {})
        total, resolved, breached = r.get('total', 0), r.get('resolved', 0), r.get('breached', 0)
        rows.append({
            'user': u, 'name': u.get_full_name() or u.username, 'is_admin': u.is_superuser,
            'total': total, 'resolved': resolved, 'breached': breached, 'urgent': r.get('urgent', 0),
            'resolution_rate': _pct(resolved, total),
            'sla_rate': _pct(total - breached, total),
            'avg_min': r.get('avg_min'), 'avg_label': fmt_minutes(r.get('avg_min')),
            'open_now': open_now.get(u.pk, 0),
            'comments': comments.get(u.pk, 0), 'signoffs': signoffs.get(u.pk, 0),
        })

    total = sum(r['total'] for r in rows)
    resolved = sum(r['resolved'] for r in rows)
    breached = sum(r['breached'] for r in rows)
    team_avg = tickets.filter(status__in=DONE_STATES).exclude(assigned_to=None).aggregate(
        a=Avg('resolution_time_minutes'))['a']
    team = {'total': total, 'resolved': resolved, 'breached': breached,
            'resolution_rate': _pct(resolved, total), 'sla_rate': _pct(total - breached, total),
            'avg_label': fmt_minutes(team_avg), 'open_now': sum(r['open_now'] for r in rows),
            'unassigned': tickets.filter(assigned_to=None).count(),
            'unassigned_open': Ticket.objects.filter(assigned_to=None, status__in=OPEN_STATES).count()}

    def best(pool, key, reverse=True):
        pool = [r for r in pool if key(r) is not None]
        return sorted(pool, key=lambda r: (key(r), r['total']), reverse=reverse)[0] if pool else None

    top = {
        'most_resolved': best([r for r in rows if r['resolved']], lambda r: r['resolved']),
        'fastest':       best([r for r in rows if r['resolved'] >= 3], lambda r: r['avg_min'], reverse=False),
        'best_sla':      best([r for r in rows if r['total'] >= 5], lambda r: r['sla_rate']),
        'busiest':       best([r for r in rows if r['open_now']], lambda r: r['open_now']),
    }
    return {'rows': rows, 'team': team, 'top': top}


# ── Request demand ────────────────────────────────────────────────────────────

_METRICS = dict(
    count=Count('id'),
    open=Count('id', filter=Q(status__in=OPEN_STATES)),
    resolved=Count('id', filter=Q(status__in=DONE_STATES)),
    urgent=Count('id', filter=Q(priority__in=['HIGH', 'CRITICAL'])),
    avg_min=Avg('resolution_time_minutes', filter=Q(status__in=DONE_STATES)),
    last=Max('created_at'),
)


def _norm(value):
    return ' '.join((value or '').split()).lower()


def best_spelling(counts):
    """Of several spellings that differ only in case/spacing, the most used one (ties: the one with capitals)."""
    return max(counts.items(), key=lambda kv: (kv[1], any(c.isupper() for c in kv[0]), kv[0]))[0]


def _row(label, agg, headcount=None, extra=None):
    agg = agg or {}
    count = agg.get('count', 0)
    row = {'label': label, 'count': count, 'open': agg.get('open', 0), 'resolved': agg.get('resolved', 0),
           'urgent': agg.get('urgent', 0), 'avg_min': agg.get('avg_min'),
           'avg_label': fmt_minutes(agg.get('avg_min')), 'last': agg.get('last'),
           'headcount': headcount, 'per_head': round(count / headcount, 2) if headcount else None}
    row.update(extra or {})
    return row


def demand(by, period, sort='count', direction='desc'):
    """
    Request volume by 'user', 'office', 'department' or 'section' — including those
    with no requests at all, so "who asks least" is as visible as "who asks most".
    """
    from accounts.models import UserProfile
    tickets = in_period(Ticket.objects.all(), 'created_at', period)
    total = tickets.count()
    unlinked = tickets.filter(requester=None).count()      # logged for callers who are not users
    rows = []

    if by == 'user':
        agg = {r['requester']: r for r in tickets.exclude(requester=None).values('requester').annotate(**_METRICS)}
        people = User.objects.filter(Q(is_active=True, is_staff=False) | Q(pk__in=list(agg))) \
                             .select_related('profile')
        for u in people:
            prof = getattr(u, 'profile', None)
            rows.append(_row(u.get_full_name() or u.username, agg.get(u.pk), headcount=1, extra={
                'user_id': u.pk, 'is_staff': u.is_staff,
                'office': getattr(prof, 'office', ''), 'department': getattr(prof, 'department', ''),
                'section': getattr(prof, 'section', '')}))
    else:
        field = GROUP_FIELDS[by]
        merged = {}                       # 'Town Hall' / 'town hall ' / 'TOWN HALL' are one group
        for r in tickets.values(field).annotate(**_METRICS):
            key = _norm(r[field]) or NOT_SET
            label = ' '.join((r[field] or '').split()) or NOT_SET
            m = merged.setdefault(key, {'label': label, 'count': 0, 'open': 0, 'resolved': 0, 'urgent': 0,
                                        '_res_minutes': 0.0, '_res_n': 0, 'last': None, '_spell': {}})
            m['_spell'][label] = m['_spell'].get(label, 0) + r['count']
            for k in ('count', 'open', 'resolved', 'urgent'):
                m[k] += r[k]
            if r['avg_min'] is not None and r['resolved']:
                m['_res_minutes'] += r['avg_min'] * r['resolved']; m['_res_n'] += r['resolved']
            if r['last'] and (not m['last'] or r['last'] > m['last']):
                m['last'] = r['last']
        for m in merged.values():
            m['avg_min'] = (m['_res_minutes'] / m['_res_n']) if m['_res_n'] else None
            m['label'] = best_spelling(m['_spell'])

        heads = {}
        for r in UserProfile.objects.filter(user__is_active=True, user__is_staff=False) \
                                    .exclude(**{by: ''}).values(by).annotate(n=Count('id')):
            k = _norm(r[by])
            heads.setdefault(k, {'label': ' '.join(r[by].split()), 'n': 0})['n'] += r['n']
        for k in set(merged) | set(heads):
            label = (heads.get(k) or merged.get(k))['label']
            rows.append(_row(label, merged.get(k), headcount=(heads.get(k) or {}).get('n')))

    for r in rows:
        r['share'] = round(100 * r['count'] / total, 1) if total else 0

    keyfn = {
        'name':  lambda r: r['label'].lower(),
        'rate':  lambda r: (r['per_head'] is None, r['per_head'] or 0),
        'avg':   lambda r: (r['avg_min'] is None, r['avg_min'] or 0),
        'last':  lambda r: (r['last'] is None, r['last'].timestamp() if r['last'] else 0),
    }.get(sort)
    reverse = direction != 'asc'
    if sort == 'count' or keyfn is None:
        sort = 'count'
        rows.sort(key=lambda r: (r['count'], r['label'].lower()) if not reverse else (-r['count'], r['label'].lower()))
    elif sort == 'name':
        rows.sort(key=keyfn, reverse=reverse)
    else:
        # entries with no value always sink to the bottom, whichever direction is chosen
        rows.sort(key=lambda r: r['label'].lower())
        have = [r for r in rows if keyfn(r)[0] is False]
        none = [r for r in rows if keyfn(r)[0] is True]
        have.sort(key=keyfn, reverse=reverse)
        rows = have + none

    # "(not set)" is a data-quality bucket, not an office/department/section, so it can never be the
    # busiest or the quietest group — it is reported separately.
    real = [r for r in rows if r['label'] != NOT_SET]
    active = [r for r in real if r['count']]
    not_set = next((r['count'] for r in rows if r['label'] == NOT_SET), 0)
    summary = {
        'total': total, 'unlinked': unlinked, 'groups': len(real), 'active': len(active),
        'silent': len(real) - len(active), 'not_set': not_set,
        'most': max(active, key=lambda r: (r['count'], r['label'])) if active else None,
        'fewest': min(active, key=lambda r: (r['count'], r['label'])) if active else None,
        'average': round(total / len(real), 1) if real else 0,
    }
    return {'rows': rows, 'summary': summary, 'by': by, 'sort': sort,
            'direction': 'desc' if reverse else 'asc'}


# ── Drill-down: tickets belonging to one row of the demand report ─────────────

NONE_TOKEN = '__none__'          # ?office=__none__  ->  tickets with no office recorded


def filter_by_group(qs, column, label):
    """
    Restrict `qs` to the tickets the demand report counts under `label` for `column`
    (requester_office / requester_department / requester_section). The report merges spellings that
    differ only in case or spacing, so this matches the same way and the numbers always agree.
    """
    wanted = '' if label == NONE_TOKEN else _norm(label)
    raw = qs.model.objects.values_list(column, flat=True).distinct()
    matches = [v for v in raw if _norm(v) == wanted]
    return qs.filter(**{f'{column}__in': matches}) if matches else qs.none()
