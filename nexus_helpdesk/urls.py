from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView
from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required


@staff_member_required
def admin_about(request):
    return render(request, 'admin/about.html')


@staff_member_required
def admin_howto(request):
    return render(request, 'admin/howto.html')


urlpatterns = [
    path('admin/about/', admin_about, name='admin_about'),
    path('admin/howto/', admin_howto, name='admin_howto'),
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(url='/dashboard/', permanent=False)),
    path('dashboard/', include('helpdesk.urls')),
    path('voip/', include('voip.urls')),
    path('accounts/', include('accounts.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) \
  + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

admin.site.site_header = "BeitDesk Administration — Municipality of Beitbridge"
admin.site.site_title = "BeitDesk Admin"
admin.site.index_title = "IT Help Desk & Support Management"
