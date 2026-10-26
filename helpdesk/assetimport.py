"""
Bulk import of the IT asset registry from a CSV / Excel inventory (e.g. Inventory_Gadgets.xlsx).

parse_inventory()  read the file into normalised rows (types, dates, costs, status understood)
plan_import()      compare with the registry WITHOUT changing anything (this is the preview)
apply_import()     create the assets

The rules are the same as the Register Asset form: a serial number may only exist once
(placeholder serials such as "N/A" may repeat), and nothing already in the registry is ever
overwritten — rows that match an existing asset are skipped and reported.
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import Asset
from .tabular import ImportProblem, norm, numbered_rows, read_table

MAX_ROWS = 5000
SERIAL_PLACEHOLDERS = {'', 'n/a', 'na', 'none', 'nil', 'unknown', 'tbc', 'tbd', '-', '--'}   # may repeat
LIMITS = {'tag': 30, 'name': 150, 'serial': 100, 'imei': 40, 'manufacturer': 100, 'model': 100,
          'department': 100, 'section': 100, 'location': 150, 'owner': 150}

ALIASES = {
    'serial':       {'serial number', 'serial no', 'serial', 'serial #', 'serial numbers', 's/n', 'sn', 'serialnumber'},
    'name':         {'device name', 'name', 'device', 'asset name', 'description', 'item', 'equipment', 'device description'},
    'type':         {'type', 'asset type', 'category', 'device type', 'kind'},
    'date':         {'date', 'purchase date', 'date purchased', 'purchased', 'date acquired', 'acquired',
                     'date of purchase', 'date issued', 'date received'},
    'imei':         {'imei', 'imei number', 'imei numbers', 'imei no', 'imei no.', 'imei 1', 'imei1', 'imei number 1'},
    'imei2':        {'imei 2', 'imei2', 'imei number 2', 'imei no 2'},
    'imei3':        {'imei 3', 'imei3', 'imei number 3', 'imei no 3'},
    'owner':        {'owner', 'assigned to', 'user', 'custodian', 'assigned', 'issued to', 'allocated to', 'assigned user'},
    'tag':          {'asset tag', 'tag', 'asset id', 'asset no', 'asset number', 'tag number', 'asset tag number'},
    'manufacturer': {'manufacturer', 'make', 'brand', 'vendor'},
    'model':        {'model', 'model number', 'model no'},
    'department':   {'department', 'dept'},
    'section':      {'section'},
    'location':     {'location', 'office', 'site', 'room', 'building'},
    'status':       {'status', 'condition'},
    'cost':         {'purchase cost', 'cost', 'price', 'value', 'amount', 'purchase price'},
    'warranty':     {'warranty expiry', 'warranty', 'warranty end', 'warranty expires', 'warranty until'},
    'notes':        {'notes', 'comments', 'remarks', 'note', 'comment'},
}

TEMPLATE_HEADER = ['Serial number', 'Device name', 'Type', 'Date', 'Owner', 'IMEI 1', 'IMEI 2', 'IMEI 3', 'Asset tag',
                   'Manufacturer', 'Model', 'Department', 'Section', 'Location', 'Status',
                   'Purchase cost', 'Warranty expiry', 'Notes']
TEMPLATE_EXAMPLES = [
    ['SN123456', 'Dell Latitude 5420', 'Laptop', '2024-03-15', 'T. Moyo', '', '', '', '', 'Dell', 'Latitude 5420',
     'Finance', 'Revenue', 'Town Hall', 'Active', '850.00', '2027-03-15', ''],
    ['R5CT8392YCJ', 'Galaxy A23', 'Mobile', '15/03/2024', 'R. Dube', '351014293379976', '352191243379972', '', '',
     '', '', '', '', '', '', '', '', ''],
]


# ── Understanding the values ──────────────────────────────────────────────────

def map_type(value, name=''):
    """('LAPTOP'|..., recognised?) from the spreadsheet's type (falling back to the device name)."""
    codes = {c: c for c, _ in Asset.TYPE_CHOICES}
    codes.update({l.lower(): c for c, l in Asset.TYPE_CHOICES})
    v = norm(value).lower()
    if v.upper() in codes:
        return v.upper(), True
    if v in codes:
        return codes[v], True
    text = f'{v} {norm(name).lower()}'
    tablet = bool(re.search(r'\bipad\b|\btablet\b|\btab\s?[a-z0-9]', text))
    rules = [
        (r'laptop|notebook|ultrabook|macbook|elitebook|omnibook', 'LAPTOP'),
        (r'printer|copier|scanner|plotter|\bmfp\b|laserjet', 'PRINTER'),
        (r'server', 'SERVER'),
        (r'router|switch|access point|\bap\b|firewall|modem|network|wi-?fi', 'NETWORK'),
        (r'monitor|screen|display', 'MONITOR'),
        (r'desktop|\bpc\b|computer|workstation|all-in-one|\baio\b', 'DESKTOP'),
    ]
    if v:
        for pattern, code in rules:
            if re.search(pattern, v):
                return code, True
        if tablet:
            return 'TABLET', True
        if re.search(r'mobile|phone|cell|handset|voip|iphone|galaxy', v):
            return 'PHONE', True
        return 'OTHER', False
    # no type column value: infer from the device name
    for pattern, code in rules:
        if re.search(pattern, text):
            return code, True
    if tablet:
        return 'TABLET', True
    if re.search(r'phone|galaxy|iphone', text):
        return 'PHONE', True
    return 'OTHER', False


def map_status(value):
    v = norm(value).lower()
    if not v:
        return 'ACTIVE', True
    if v.upper() in dict(Asset.STATUS_CHOICES):
        return v.upper(), True
    for pattern, code in ((r'repair|faulty|broken|maintenance|fault', 'REPAIR'),
                          (r'retire|dispos|written off|scrap|obsolete|decommission', 'RETIRED'),
                          (r'missing|lost|stolen', 'MISSING'),
                          (r'active|in use|working|good|issued|ok|operational', 'ACTIVE')):
        if re.search(pattern, v):
            return code, True
    return 'ACTIVE', False


def infer_manufacturer(name):
    n = name.lower()
    for pattern, maker in ((r'galaxy|samsung', 'Samsung'), (r'dell|inspiron|latitude|optiplex', 'Dell'),
                           (r'^hp\b|elitebook|omnibook|pavilion|laserjet|xflip|probook', 'HP'),
                           (r'ipad|iphone|macbook', 'Apple'), (r'lenovo|thinkpad|ideapad', 'Lenovo'),
                           (r'^acer\b', 'Acer'), (r'^asus\b', 'Asus'), (r'huawei', 'Huawei')):
        if re.search(pattern, n):
            return maker
    return ''


_DATE_FORMATS = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%d/%m/%y', '%d-%m-%y',
                 '%d %b %Y', '%d %B %Y', '%b %d, %Y', '%B %d, %Y', '%Y/%m/%d')


def parse_date(value):
    """Return (date | None, understood?). Day comes first in written dates (15/03/2024 = 15 March)."""
    if value is None or value == '':
        return None, True
    if isinstance(value, datetime):
        return value.date(), True
    if isinstance(value, date):
        return value, True
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:          # Excel serial number
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date(), True
    text = norm(value)
    if not text:
        return None, True
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date(), True
        except ValueError:
            continue
    m = re.match(r'^(\d{4}-\d{2}-\d{2})[ T]', text)                               # 2024-03-15 00:00:00
    if m:
        return datetime.strptime(m.group(1), '%Y-%m-%d').date(), True
    return None, False


def parse_cost(value):
    if value is None or value == '':
        return '', True
    text = re.sub(r'[^\d.\-]', '', norm(value).replace(',', ''))
    try:
        d = Decimal(text)
    except InvalidOperation:
        return '', False
    if d < 0 or d >= Decimal('100000000'):
        return '', False
    return f'{d:.2f}', True


# ── Reading the file ──────────────────────────────────────────────────────────

def parse_inventory(filename, raw):
    """
    Read a CSV / Excel inventory. Returns (rows, columns, notices) where each row is a dict:
    line, tag, serial, name, type, status, manufacturer, model, department, section, location,
    owner, date, cost, warranty, notes, warnings.
    """
    table = read_table(filename, raw)
    numbered = numbered_rows(table)
    if not numbered:
        raise ImportProblem('The file has no data.')
    header = [norm(c).lower() for c in numbered[0][1]]
    index = {}
    for key, names in ALIASES.items():
        for i, h in enumerate(header):
            if h in names and key not in index:
                index[key] = i
    if 'serial' not in index and 'name' not in index:
        raise ImportProblem('Could not find a "Serial number" or "Device name" column. The first row must be a '
                            'header row. Download the template to see the columns that are understood.')
    if len(numbered) - 1 > MAX_ROWS:
        raise ImportProblem(f'Too many rows ({len(numbered) - 1}); the limit is {MAX_ROWS} per file.')

    def raw_cell(r, key):
        return r[index[key]] if key in index and index[key] < len(r) else ''

    rows, notices = [], []
    for n, r in numbered[1:]:
        text = lambda k: norm(raw_cell(r, k))
        serial, name = text('serial'), text('name')
        if not serial and not name:
            notices.append(f'Row {n} has neither a serial number nor a device name and was skipped.')
            continue
        warnings, notes = [], []

        atype, known = map_type(text('type'), name)
        if not known:
            warnings.append(f"type '{text('type') or name}' was not recognised — filed as Other")
        status, known = map_status(text('status'))
        if not known:
            warnings.append(f"status '{text('status')}' was not recognised — set to Active")
        bought, ok = parse_date(raw_cell(r, 'date'))
        if not ok:
            warnings.append(f"date '{text('date')}' was not understood — left blank")
        warranty, ok = parse_date(raw_cell(r, 'warranty'))
        if not ok:
            warnings.append(f"warranty date '{text('warranty')}' was not understood — left blank")
        cost, ok = parse_cost(raw_cell(r, 'cost'))
        if not ok:
            warnings.append(f"cost '{text('cost')}' was not understood — left blank")

        imeis = ' '.join(text(k) for k in ('imei', 'imei2', 'imei3')).split()
        slots = (imeis[:3] + ['', '', ''])[:3]
        if len(imeis) > 3:
            notes.append('More IMEIs: ' + ' / '.join(imeis[3:]))
        if not name:
            name = f"{dict(Asset.TYPE_CHOICES)[atype]} (name not recorded)" if atype != 'OTHER' else 'Device (name not recorded)'
            notes.append('Device name was blank in the source inventory.')
        if text('notes'):
            notes.insert(0, text('notes'))

        rows.append({
            'line': n, 'tag': text('tag'), 'serial': serial, 'name': name, 'type': atype, 'status': status,
            'manufacturer': text('manufacturer') or infer_manufacturer(name), 'model': text('model'),
            'department': text('department'), 'section': text('section'), 'location': text('location'),
            'owner': text('owner'), 'imei': slots[0], 'imei2': slots[1], 'imei3': slots[2],
            'date': bought.isoformat() if bought else '',
            'cost': cost, 'warranty': warranty.isoformat() if warranty else '',
            'notes': '\n'.join(notes), 'warnings': warnings})
    return rows, [k for k in index], notices


# ── Planning (the preview) ────────────────────────────────────────────────────

def imei_key(value):
    return norm(value).lower()


def imei_base(value):
    """'358277320714563/01' -> '358277320714563' (the number without its software-version suffix)."""
    return imei_key(value).split('/')[0]


def _prefix_ok(prefix):
    return bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,19}', prefix or ''))


def _next_number(prefix):
    """Highest number already used with this prefix (PREFIX-007 -> 7); the next tag is one above."""
    pat = re.compile(rf'^{re.escape(prefix)}-(\d+)$', re.I)
    top = 0
    for tag in Asset.objects.filter(asset_tag__istartswith=prefix + '-').values_list('asset_tag', flat=True):
        m = pat.match(tag)
        if m:
            top = max(top, int(m.group(1)))
    return top + 1


def plan_import(rows, prefix):
    """
    Decide, row by row, what would happen. Nothing is written.
    Returns {'new': [...rows with a predicted 'tag'], 'duplicates': [...], 'errors': [...], 'by_type': {...}}.
    """
    if not _prefix_ok(prefix):
        raise ImportProblem('The tag prefix may only contain letters, numbers, "-" and "_" (up to 20 characters).')
    existing_serial = {s.lower(): (t, n) for s, t, n in
                       Asset.objects.exclude(serial_number='').values_list('serial_number', 'asset_tag', 'name')}
    existing_tags = {t.lower() for t in Asset.objects.values_list('asset_tag', flat=True)}
    existing_imei, existing_base = {}, {}
    for tag, *numbers in Asset.objects.exclude(imei='').values_list('asset_tag', 'imei', 'imei2', 'imei3'):
        for n in filter(None, numbers):
            existing_imei[imei_key(n)] = tag
            existing_base.setdefault(imei_base(n), tag)
    seen_serial, seen_tags, seen_imei, seen_base = set(), set(), {}, {}
    number = _next_number(prefix)
    plan = {'new': [], 'duplicates': [], 'errors': [], 'by_type': {}}

    for r in rows:
        key = r['serial'].lower()
        too_long = [lbl for lbl, v in (('tag', r['tag']), ('name', r['name']), ('serial number', r['serial']),
                                      ('IMEI', max((r['imei'], r['imei2'], r['imei3']), key=len)),
                                      ('manufacturer', r['manufacturer']), ('model', r['model']),
                                      ('department', r['department']), ('section', r['section']),
                                      ('location', r['location']), ('owner', r['owner']))
                    if len(v) > LIMITS[{'serial number': 'serial', 'IMEI': 'imei'}.get(lbl, lbl)]]
        if too_long:
            plan['errors'].append(f"Row {r['line']}: {', '.join(too_long)} is too long.")
            continue
        if key not in SERIAL_PLACEHOLDERS:
            if key in existing_serial:
                tag, nm = existing_serial[key]
                plan['duplicates'].append({'row': r, 'reason': f'serial number already registered to {tag} ({nm})'})
                continue
            if key in seen_serial:
                plan['duplicates'].append({'row': r, 'reason': 'serial number appears earlier in this file'})
                continue
            seen_serial.add(key)
        numbers = [n for n in (r['imei'], r['imei2'], r['imei3']) if n]
        clash = next((n for n in numbers if imei_key(n) in existing_imei or imei_key(n) in seen_imei), None)
        if clash is not None:
            owner = existing_imei.get(imei_key(clash)) or seen_imei[imei_key(clash)]
            plan['duplicates'].append({'row': r, 'reason': f'IMEI {clash} is already registered to {owner}'})
            continue
        near = []
        for n in numbers:
            other = existing_base.get(imei_base(n)) or seen_base.get(imei_base(n))
            if other:
                near.append(f'IMEI {n} matches {other} apart from its suffix — check one of them for a typing slip')
        tag = r['tag']
        if tag:
            if tag.lower() in existing_tags or tag.lower() in seen_tags:
                plan['duplicates'].append({'row': r, 'reason': f'asset tag {tag} is already in use'})
                continue
        else:
            while f'{prefix}-{number:03d}'.lower() in existing_tags | seen_tags:
                number += 1
            tag = f'{prefix}-{number:03d}'
            number += 1
        seen_tags.add(tag.lower())
        for n in numbers:
            seen_imei[imei_key(n)] = tag
            seen_base.setdefault(imei_base(n), tag)
        plan['new'].append({**r, 'tag': tag, 'warnings': list(r['warnings']) + near})
        plan['by_type'][r['type']] = plan['by_type'].get(r['type'], 0) + 1
    return plan


def apply_import(plan, user=None):
    """Create the planned assets. Returns the list of created Asset objects."""
    created = []
    with transaction.atomic():
        for r in plan['new']:
            created.append(Asset.objects.create(
                asset_tag=r['tag'], name=r['name'], asset_type=r['type'], status=r['status'],
                serial_number=r['serial'], imei=r['imei'], imei2=r['imei2'], imei3=r['imei3'],
                manufacturer=r['manufacturer'], model=r['model'],
                department=r['department'], section=r['section'], location=r['location'],
                assigned_to_name=r['owner'],
                purchase_date=date.fromisoformat(r['date']) if r['date'] else None,
                purchase_cost=Decimal(r['cost']) if r['cost'] else None,
                warranty_expiry=date.fromisoformat(r['warranty']) if r['warranty'] else None,
                notes=r['notes'], created_by=user))
    return created
