from django.apps import AppConfig


class HelpdeskConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'helpdesk'
    verbose_name = 'Help Desk'

    def ready(self):
        import helpdesk.signals  # noqa — registers signal handlers
