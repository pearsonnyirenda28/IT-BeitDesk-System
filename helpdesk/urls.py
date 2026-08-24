from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('tickets/', views.ticket_list, name='ticket_list'),
    path('tickets/create/', views.ticket_create, name='ticket_create'),
    path('tickets/print/', views.print_tickets, name='print_tickets'),
    path('tickets/<str:ticket_id>/', views.ticket_detail, name='ticket_detail'),
    path('tickets/<str:ticket_id>/edit/', views.ticket_edit, name='ticket_edit'),
    path('tickets/<str:ticket_id>/print/', views.print_ticket_single, name='print_ticket_single'),
    path('reports/', views.reports, name='reports'),
    path('audit/', views.audit_log, name='audit_log'),
    path('glossary/', views.glossary, name='glossary'),
    path('database/', views.database_switcher, name='database_switcher'),
    path('api/stats/', views.api_stats, name='api_stats'),
    path('api/suggest/', views.api_suggest, name='api_suggest'),
    path('api/learn/', views.api_learn, name='api_learn'),
    path('api/notifications/', views.api_notifications, name='api_notifications'),
    path('api/push/subscribe/', views.api_push_subscribe, name='api_push_subscribe'),
    path('api/theme/', views.api_theme, name='api_theme'),
    path('api/bulk/', views.api_bulk_action, name='api_bulk_action'),
]
