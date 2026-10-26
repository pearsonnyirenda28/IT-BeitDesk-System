"""
Bulk handling of organisation data (office / department / section):

  * import from a CSV or Excel file   — parse -> plan (preview) -> apply
  * export the current list           — a ready-made file to fill in and re-import
  * backfill older tickets            — copy a requester's organisation onto tickets raised
                                        before those details were recorded

Importing never deletes anything by accident: a blank cell means "leave it alone" unless the
administrator explicitly asks for blanks to clear existing values.
"""
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q

from .tabular import ImportProblem, MAX_BYTES, norm as _norm, numbered_rows, read_table

FIELDS = ('office', 'department', 'section')
TICKET_COLUMN = {'office': 'requester_office', 'department': 'requester_department',
                 'section': 'requester_section'}
MAX_ROWS = 5000
MAX_LEN = 100
ALIASES = {
    'username':   {'username', 'user', 'user name', 'login', 'login name', 'userid', 'user id', 'account'},
    'office':     {'office', 'offices'},
    'department': {'department', 'departments', 'dept'},
    'section':    {'section', 'sections'},
}


# ── Reading the file ──────────────────────────────────────────────────────────

def parse_upload(filename, raw):
    """
    Read an uploaded CSV / XLSX into (rows, columns, warnings).
    rows: [{'line': n, 'username': str, 'office': str, ...}] with only the organisation columns present.
    """
    table = read_table(filename, raw)
    # Keep each row's real position in the file so messages point at the right line
    numbered = numbered_rows(table)
    if not numbered:
        raise ImportProblem('The file has no data.')
    header = [_norm(c).lower() for c in numbered[0][1]]
    index = {}
    for key, names in ALIASES.items():
        for i, h in enumerate(header):
            if h in names and key not in index:
                index[key] = i
    if 'username' not in index:
        raise ImportProblem('Could not find a "username" column. The first row must be a header row, '
                            'for example:  username, office, department, section')
    columns = [f for f in FIELDS if f in index]
    if not columns:
        raise ImportProblem('Could not find any of the columns "office", "department" or "section".')
    if len(numbered) - 1 > MAX_ROWS:
        raise ImportProblem(f'Too many rows ({len(numbered) - 1}); the limit is {MAX_ROWS} per file.')

    rows, warnings = [], []
    for n, r in numbered[1:]:
        cell = lambda k: _norm(r[index[k]]) if index[k] < len(r) else ''
        username = cell('username')
        if not username:
            warnings.append(f'Row {n} has no username and was skipped.')
            continue
        rows.append({'line': n, 'username': username, **{f: cell(f) for f in columns}})
    return rows, columns, warnings


# ── Planning and applying ─────────────────────────────────────────────────────

def plan_import(rows, columns, clear_blanks=False):
    """
    Compare the file with the database without changing anything.
    Returns {'updates', 'unchanged', 'unknown', 'inactive', 'errors', 'duplicates'}.
    """
    active = {u.username.lower(): u for u in User.objects.filter(is_active=True).select_related('profile')}
    inactive = {n.lower() for n in User.objects.filter(is_active=False).values_list('username', flat=True)}

    latest, duplicates = {}, 0
    for r in rows:
        key = r['username'].lower()
        if key in latest:
            duplicates += 1                       # a person listed twice: the later row wins
        latest[key] = r

    plan = {'updates': [], 'unchanged': 0, 'unknown': [], 'inactive': [], 'errors': [], 'duplicates': duplicates}
    for key, r in latest.items():
        user = active.get(key)
        if user is None:
            (plan['inactive'] if key in inactive else plan['unknown']).append(r['username'])
            continue
        too_long = [f for f in columns if len(r.get(f, '')) > MAX_LEN]
        if too_long:
            plan['errors'].append(f"Row {r['line']} ({r['username']}): {', '.join(too_long)} is longer than {MAX_LEN} characters.")
            continue
        prof = getattr(user, 'profile', None)
        changes = {}
        for f in columns:
            new, old = r.get(f, ''), getattr(prof, f, '') if prof else ''
            if new == '' and not clear_blanks:
                continue                          # blank cell = leave as it is
            if new != old:
                changes[f] = (old, new)
        if changes:
            plan['updates'].append({'user': user, 'name': user.get_full_name() or user.username, 'changes': changes})
        else:
            plan['unchanged'] += 1
    plan['updates'].sort(key=lambda u: u['name'].lower())
    return plan


def apply_import(plan):
    """Write the planned changes. Returns the number of people updated."""
    from accounts.models import UserProfile
    with transaction.atomic():
        for item in plan['updates']:
            prof, _ = UserProfile.objects.get_or_create(user=item['user'])
            for f, (_old, new) in item['changes'].items():
                setattr(prof, f, new)
            prof.save(update_fields=list(item['changes']))
    return len(plan['updates'])


# ── Export ────────────────────────────────────────────────────────────────────

def export_rows():
    """Header + one row per active user — the file to fill in and upload again."""
    out = [['username', 'full name', 'role', 'office', 'department', 'section']]
    for u in User.objects.filter(is_active=True).select_related('profile').order_by('first_name', 'last_name', 'username'):
        prof = getattr(u, 'profile', None)
        role = 'Administrator' if u.is_superuser else ('IT staff' if u.is_staff else 'User')
        out.append([u.username, u.get_full_name(), role] + [getattr(prof, f, '') for f in FIELDS])
    return out


# ── Backfill older tickets ────────────────────────────────────────────────────

def backfill_ticket_org(alias='default', apply=False):
    """
    Fill the office / department / section snapshot on tickets raised before those details
    were recorded, using the requester's profile. Only blank values are filled — anything
    already on a ticket is never overwritten.

    People are read from the main database; tickets are updated in `alias`.
    Returns {'users': n people with something to fill, 'tickets': n tickets, 'fields': n values}.
    """
    from accounts.models import UserProfile
    from .models import Ticket

    people = {}
    for p in UserProfile.objects.using('default').all():
        vals = {f: getattr(p, f) for f in FIELDS if getattr(p, f)}
        if vals:
            people[p.user_id] = vals
    tickets = Ticket.objects.using(alias)
    requesters = set(tickets.exclude(requester=None).values_list('requester_id', flat=True).distinct())

    result = {'users': 0, 'tickets': 0, 'fields': 0}
    for uid, vals in people.items():
        if uid not in requesters:
            continue
        blank = Q()
        for f in vals:
            blank |= Q(**{TICKET_COLUMN[f]: ''})
        n = tickets.filter(requester_id=uid).filter(blank).count()
        if not n:
            continue
        result['users'] += 1
        result['tickets'] += n
        if apply:
            for f, val in vals.items():
                result['fields'] += tickets.filter(requester_id=uid, **{TICKET_COLUMN[f]: ''}) \
                                           .update(**{TICKET_COLUMN[f]: val})
    return result


def estimate_backfill_after_import(plan, alias='default'):
    """
    How many older tickets the backfill WOULD fill once `plan` has been applied.
    The import is applied inside a transaction that is always rolled back, so nothing is saved.
    """
    try:
        with transaction.atomic():
            apply_import(plan)
            result = backfill_ticket_org(alias, apply=False)
            transaction.set_rollback(True)
        return result
    except Exception:
        return {'users': 0, 'tickets': 0, 'fields': 0}
