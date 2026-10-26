from django.urls import path
from . import views, views_insights, views_assetimport, quicklinks

urlpatterns = [
    # Dashboard
    path('', views.dashboard, name='dashboard'),

    # Tickets
    path('tickets/',                              views.ticket_list,         name='ticket_list'),
    path('tickets/create/',                       views.ticket_create,       name='ticket_create'),
    path('tickets/export/',                       views.export_tickets_csv,  name='export_tickets_csv'),
    path('assets/export/',                        views.export_assets_csv,   name='export_assets_csv'),
    path('tickets/<str:ticket_id>/',              views.ticket_detail,       name='ticket_detail'),
    path('tickets/<str:ticket_id>/edit/',         views.ticket_edit,         name='ticket_edit'),

    # Reports & Audit
    path('reports/',                              views.reports,             name='reports'),
    path('audit/',                                views.audit_log,           name='audit_log'),
    path('users/<int:user_id>/activity/',         views.user_activity,       name='user_activity'),

    # Knowledge Base
    path('kb/',                                   views.kb_list,             name='kb_list'),
    path('kb/create/',                            views.kb_create,           name='kb_create'),
    path('kb/<int:pk>/',                          views.kb_detail,           name='kb_detail'),
    path('kb/<int:pk>/edit/',                     views.kb_edit,             name='kb_edit'),

    # Asset Registry
    path('assets/',                               views.asset_list,          name='asset_list'),
    path('assets/create/',                        views.asset_create,        name='asset_create'),
    path('assets/<int:pk>/',                      views.asset_detail,        name='asset_detail'),
    path('assets/<int:pk>/edit/',                 views.asset_edit,          name='asset_edit'),

    # Database Backup
    path('backup/',                               views.db_backup,           name='db_backup'),

    # Utilities
    path('glossary/',                             views.glossary,            name='glossary'),
    path('database/',                             views.database_switcher,   name='database_switcher'),

    # Requester Portal
    path('portal/',                               views.portal,              name='portal'),
    path('portal/new/',                           views.portal_create,       name='portal_create'),
    path('portal/ticket/<str:ticket_id>/',        views.portal_ticket,       name='portal_ticket'),

    # Recurring Tickets
    path('recurring/',                            views.recurring_list,      name='recurring_list'),
    path('recurring/create/',                     views.recurring_create,    name='recurring_create'),
    path('recurring/<int:pk>/toggle/',            views.recurring_toggle,    name='recurring_toggle'),
    path('recurring/<int:pk>/delete/',            views.recurring_delete,    name='recurring_delete'),

    # Ticket Templates
    path('templates/',                            views.template_list,       name='template_list'),
    path('templates/create/',                     views.template_create,     name='template_create'),
    path('templates/<int:pk>/edit/',              views.template_edit,       name='template_edit'),
    path('templates/<int:pk>/delete/',            views.template_delete,     name='template_delete'),

    # Tags
    path('tags/',                                 views.tag_list,            name='tag_list'),
    path('tags/<str:tag_name>/',                  views.tag_tickets,         name='tag_tickets'),

    # API
    path('api/tags/',                             views.api_tags,            name='api_tags'),
    path('api/tickets/<str:ticket_id>/tags/',     views.api_ticket_tags,     name='api_ticket_tags'),
    path('api/assets/check-serial/',               views.api_check_serial,    name='api_check_serial'),
    # Organisation, analytics and the new-requests queue
    path('organisation/',                         views_insights.organisation,      name='organisation'),
    path('organisation/import/',                  views_insights.organisation_import,   name='organisation_import'),
    path('organisation/export/',                  views_insights.organisation_export,   name='organisation_export'),
    path('organisation/backfill/',                views_insights.organisation_backfill, name='organisation_backfill'),
    path('analytics/staff/',                      views_insights.analytics_staff,   name='analytics_staff'),
    path('analytics/requests/',                   views_insights.analytics_demand,  name='analytics_demand'),
    path('queue/',                                views_insights.new_requests,      name='new_requests'),
    path('queue/<str:ticket_id>/assign/',         views_insights.queue_assign,      name='queue_assign'),
    path('queue/<str:ticket_id>/take/',           views_insights.queue_take,        name='queue_take'),
    # Quick request links (personal expiring links; the public page is /r/<token>/ in the project urls)
    path('quick-links/',                          quicklinks.quick_links,           name='quick_links'),
    path('quick-links/all/',                      quicklinks.quick_links_admin,     name='quick_links_admin'),
    path('quick-links/<int:pk>/revoke/',          quicklinks.quick_link_revoke,     name='quick_link_revoke'),
    path('assets/import/',                        views_assetimport.asset_import,          name='asset_import'),
    path('assets/import/template/',               views_assetimport.asset_import_template, name='asset_import_template'),
    path('api/stats/',                            views.api_stats,           name='api_stats'),
    path('api/suggest/',                          views.api_suggest,         name='api_suggest'),
    path('api/learn/',                            views.api_learn,           name='api_learn'),
    path('api/notifications/',                    views.api_notifications,       name='api_notifications'),
    path('api/notifications/read/',               views.api_notifications_read,  name='api_notifications_read'),
    path('api/push/subscribe/',                   views.api_push_subscribe,      name='api_push_subscribe'),
    path('api/theme/',                            views.api_theme,           name='api_theme'),
    path('api/templates/',                        views.api_templates,       name='api_templates'),
    path('api/templates/<int:pk>/use/',           views.api_template_use,    name='api_template_use'),
    path('api/bulk/',                             views.api_bulk_action,     name='api_bulk_action'),
]
