from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView
from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.http import FileResponse, HttpResponse, Http404
import os


@staff_member_required
def admin_about(request):
    return render(request, 'admin/about.html')


@staff_member_required
def admin_howto(request):
    return render(request, 'admin/howto.html')


def service_worker(request):
    """Serve service worker from root — required for full PWA scope."""
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'pwa', 'sw.js')
    try:
        response = FileResponse(
            open(sw_path, 'rb'),
            content_type='application/javascript',
        )
        response['Service-Worker-Allowed'] = '/'
        response['Cache-Control'] = 'no-cache'
        return response
    except FileNotFoundError:
        raise Http404


def pwa_manifest(request):
    """Serve the PWA manifest."""
    manifest_path = os.path.join(settings.BASE_DIR, 'static', 'pwa', 'manifest.json')
    try:
        with open(manifest_path, 'r') as f:
            return HttpResponse(f.read(), content_type='application/manifest+json')
    except FileNotFoundError:
        raise Http404


urlpatterns = [
    path('admin/about/', admin_about, name='admin_about'),
    path('admin/howto/', admin_howto, name='admin_howto'),
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(url='/dashboard/', permanent=False)),
    # PWA routes — must be at root scope
    path('sw.js', service_worker, name='service_worker'),
    path('manifest.json', pwa_manifest, name='pwa_manifest'),
    path('dashboard/', include('helpdesk.urls')),
    path('voip/', include('voip.urls')),
    path('accounts/', include('accounts.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) \
  + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

admin.site.site_header = "BeitDesk Administration — Municipality of Beitbridge"
admin.site.site_title = "BeitDesk Admin"
admin.site.index_title = "IT Help Desk & Support Management"
