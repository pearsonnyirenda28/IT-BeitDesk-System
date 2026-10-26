"""Import the asset registry from a CSV / Excel inventory: upload -> preview -> apply."""
import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import HttpResponse
from django.shortcuts import redirect, render

from . import assetimport
from .models import Asset, AuditLog
from .tabular import ImportProblem, MAX_BYTES
from .views import log_action

_SALT = 'asset-import'
DEFAULT_PREFIX = 'MOB-AST'


@login_required
def asset_import_template(request):
    """A CSV with the columns the importer understands, plus two example rows."""
    if not request.user.is_staff:
        return redirect('dashboard')
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = 'attachment; filename="asset_import_template.csv"'
    resp.write('\ufeff')
    w = csv.writer(resp)
    w.writerow(assetimport.TEMPLATE_HEADER)
    for row in assetimport.TEMPLATE_EXAMPLES:
        w.writerow(row)
    return resp


@login_required
def asset_import(request):
    if not request.user.is_staff:
        messages.error(request, 'Asset registry is for IT staff only.')
        return redirect('dashboard')
    ctx = {'prefix': DEFAULT_PREFIX, 'max_mb': MAX_BYTES // (1024 * 1024), 'max_rows': assetimport.MAX_ROWS,
           'type_labels': dict(Asset.TYPE_CHOICES)}
    if request.method != 'POST':
        return render(request, 'helpdesk/asset_import.html', ctx)

    # ── Step 2: confirm and create ────────────────────────────────────────────
    if request.POST.get('payload'):
        try:
            data = signing.loads(request.POST['payload'], salt=_SALT, max_age=1800)
        except signing.BadSignature:
            messages.error(request, 'That preview has expired. Please upload the file again.')
            return redirect('asset_import')
        try:
            plan = assetimport.plan_import(data['rows'], data['prefix'])      # re-checked against today's registry
        except ImportProblem as e:
            messages.error(request, str(e))
            return redirect('asset_import')
        created = assetimport.apply_import(plan, request.user)
        if created:
            log_action(request, AuditLog.ACTION_CREATE, 'Asset', '', 'Asset import',
                       changes={'created': [a.asset_tag for a in created][:300]},
                       notes=f'{len(created)} asset(s) imported from {data.get("file", "a file")}')
        skipped = len(plan['duplicates']) + len(plan['errors'])
        messages.success(request, f'Imported {len(created)} asset(s)'
                         + (f'; {skipped} row(s) skipped (already registered or invalid).' if skipped else '.'))
        return redirect('asset_list')

    # ── Step 1: read the file and show a preview ──────────────────────────────
    prefix = request.POST.get('prefix', '').strip() or DEFAULT_PREFIX
    ctx['prefix'] = prefix
    upload = request.FILES.get('file')
    if not upload:
        ctx['error'] = 'Choose a CSV or Excel file first.'
        return render(request, 'helpdesk/asset_import.html', ctx)
    try:
        rows, columns, notices = assetimport.parse_inventory(upload.name, upload.read(MAX_BYTES + 1))
        plan = assetimport.plan_import(rows, prefix)
    except ImportProblem as e:
        ctx['error'] = str(e)
        return render(request, 'helpdesk/asset_import.html', ctx)
    shown = plan['new'][:100]
    flagged = [r for r in plan['new'] if r['warnings']]
    ctx.update({
        'preview': True, 'plan': plan, 'shown': shown, 'more': max(0, len(plan['new']) - len(shown)),
        'file_name': upload.name, 'notices': notices, 'flagged': flagged[:15], 'flagged_total': len(flagged),
        'columns': columns, 'duplicates_shown': plan['duplicates'][:50],
        'first_tag': plan['new'][0]['tag'] if plan['new'] else '', 'last_tag': plan['new'][-1]['tag'] if plan['new'] else '',
        'payload': signing.dumps({'rows': rows, 'prefix': prefix, 'file': upload.name}, salt=_SALT, compress=True),
    })
    return render(request, 'helpdesk/asset_import.html', ctx)
