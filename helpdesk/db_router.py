"""
IT HelpDesk Year-Scoped Database Router
=====================================
When a user selects a year in the Database Switcher, all subsequent queries
are routed to that year's PostgreSQL database until they switch back.

The router uses Django's per-request database routing via thread-local storage.
The middleware reads the session and registers the correct DB alias before
each request is processed.
"""

import threading
from django.conf import settings

# Thread-local storage — each request gets its own DB alias
_thread_locals = threading.local()


def get_current_db_alias():
    """Return the database alias for the current request thread."""
    return getattr(_thread_locals, 'db_alias', 'default')


def set_current_db_alias(alias):
    """Set the database alias for the current request thread."""
    _thread_locals.db_alias = alias


def clear_current_db_alias():
    """Reset to default database."""
    _thread_locals.db_alias = 'default'


def register_year_alias(year, db_name):
    """
    Make sure the database for `year` is registered in settings.DATABASES under the
    alias ``year_<year>`` (cloned from the default config) and return that alias.
    Shared by the request middleware and by management commands.
    """
    import copy
    import os
    from django.db.utils import DEFAULT_DB_ALIAS
    alias = f'year_{year}'
    if alias not in settings.DATABASES:
        base = copy.deepcopy(settings.DATABASES[DEFAULT_DB_ALIAS])
        base.update({
            'NAME':     db_name,
            'USER':     os.environ.get('DB_USER', base.get('USER', '')),
            'PASSWORD': os.environ.get('DB_PASSWORD', base.get('PASSWORD', '')),
            'HOST':     os.environ.get('DB_HOST', base.get('HOST', 'localhost')),
            'PORT':     os.environ.get('DB_PORT', base.get('PORT', '5432')),
        })
        settings.DATABASES[alias] = base
    return alias


class YearDatabaseRouter:
    """
    Routes all read/write operations to the year-selected database when active.
    Falls back to 'default' for system tables (auth, sessions, contenttypes)
    which must always live in the default database.
    """

    # These apps/models always use the default DB regardless of year selection
    # (user accounts, sessions, admin history, and IT HelpDesk registry tables
    # must be shared across years — they are never year-specific data)
    DEFAULT_ONLY_APPS = {'auth', 'contenttypes', 'sessions', 'admin'}

    # Models within helpdesk that must always stay on default DB
    DEFAULT_ONLY_MODELS = {'databaseyear', 'glossaryterm', 'auditlog', 'category', 'tag',
                           'quickrequestlink'}

    def _use_default(self, model):
        if model._meta.app_label in self.DEFAULT_ONLY_APPS:
            return True
        if model._meta.model_name in self.DEFAULT_ONLY_MODELS:
            return True
        return False

    def db_for_read(self, model, **hints):
        if self._use_default(model):
            return 'default'
        return get_current_db_alias()

    def db_for_write(self, model, **hints):
        if self._use_default(model):
            return 'default'
        return get_current_db_alias()

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.DEFAULT_ONLY_APPS:
            return db == 'default'
        return True


class YearDatabaseMiddleware:
    """
    Reads the active year from the user's session and:
    1. Registers the year's DB config in Django's DATABASES dict (if not already there)
    2. Sets the thread-local DB alias so the router directs queries there
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        active_year = request.session.get('active_db_year')
        active_db_name = request.session.get('active_db_name')

        if active_year and active_db_name:
            alias = register_year_alias(active_year, active_db_name)

            set_current_db_alias(alias)
        else:
            clear_current_db_alias()

        response = self.get_response(request)

        # Always clean up after request
        clear_current_db_alias()
        return response
