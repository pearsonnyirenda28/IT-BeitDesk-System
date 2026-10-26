"""
BeitDesk - Municipality of Beitbridge IT Help Desk & VoIP Tracking System
Django Settings
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'beitdesk-change-this-in-production-use-env-vars')

DEBUG = True

ALLOWED_HOSTS = ['*']

# ── Multi-device / LAN access ─────────────────────────────────────────────────
# Find your PC's LAN IP: run  ipconfig  in CMD, look for IPv4 Address.
#
# FOR NORMAL HTTP (LAN only):
#   py manage.py runserver 0.0.0.0:8000
#   Other devices: http://<your-ip>:8000
#
# FOR HTTPS (required for PWA install on other devices):
#   pip install werkzeug
#   python run_https.py
#   Other devices: https://<your-ip>:8000
#
# Replace 192.168.1.171 below with YOUR actual LAN IP address.
CSRF_TRUSTED_ORIGINS = [
    'http://localhost:8000',
    'http://127.0.0.1:8000',
    'https://localhost:8000',
    'https://127.0.0.1:8000',
    # Your LAN IP — update this to match your ipconfig IPv4 address:
    'http://192.168.1.171:8000',
    'https://192.168.1.171:8000',
]

# Cookie settings — 'Lax' works for both HTTP and HTTPS on LAN
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE    = 'Lax'
CSRF_COOKIE_HTTPONLY    = False

# Allow session cookies over HTTPS
SESSION_COOKIE_SECURE = False   # Set True only if you ALWAYS use HTTPS
CSRF_COOKIE_SECURE    = False   # Set True only if you ALWAYS use HTTPS

INSTALLED_APPS = [
    'daphne',                          # Must be FIRST — before staticfiles
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    # Third-party
    'widget_tweaks',
    'channels',
    # Local apps
    'accounts',
    'helpdesk.apps.HelpdeskConfig',
    'voip',
]

MIDDLEWARE = [
    # GZip first — compresses all HTTP responses (HTML, JSON, API)
    # Reduces payload ~70-80% — critical for slow LAN/legacy machines
    'django.middleware.gzip.GZipMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise — serves static files directly, with compression+caching headers
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    # Year DB router — must come after session so it can read active year
    'helpdesk.db_router.YearDatabaseMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'helpdesk.middleware.AuditMiddleware',
]

# ── Database Router ───────────────────────────────────────────────────────────
# Routes helpdesk/voip queries to the year-selected DB when active
DATABASE_ROUTERS = ['helpdesk.db_router.YearDatabaseRouter']

ROOT_URLCONF = 'nexus_helpdesk.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'helpdesk.context_processors.global_stats',
            ],
        },
    },
]

WSGI_APPLICATION = 'nexus_helpdesk.wsgi.application'

# --- DATABASE ---
# PostgreSQL (recommended for production)
# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.postgresql',
#         'NAME': os.environ.get('DB_NAME', 'nexusdesk'),
#         'USER': os.environ.get('DB_USER', 'nexusdesk_user'),
#         'PASSWORD': os.environ.get('DB_PASSWORD', ''),
#         'HOST': os.environ.get('DB_HOST', 'localhost'),
#         'PORT': os.environ.get('DB_PORT', '5432'),
#     }
# }

# SQLite (default for development)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'nexusdesk.db',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Harare'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# ── WhiteNoise — static file serving over HTTP and HTTPS ─────────────────────
# Serves static files directly from Django without needing a web server.
# Works with werkzeug HTTPS server, standard runserver, and production.
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
WHITENOISE_USE_FINDERS  = True       # Find files in STATICFILES_DIRS at runtime
WHITENOISE_AUTOREFRESH  = True       # Reload changed files without restart (dev)
WHITENOISE_MAX_AGE      = 31536000   # Cache static files 1 year in browser (3600 in dev if preferred)
WHITENOISE_KEEP_ONLY_HASHED_FILES = False  # Keep original names for debugging
# Brotli/gzip compression of static files (CSS, JS) — served pre-compressed
# pip install whitenoise[brotli] to enable Brotli (smaller than gzip)
STORAGES = {
    "default":     {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'auth.User'
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# Email (configure for production)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Session
SESSION_COOKIE_AGE = 28800  # 8 hours
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# VoIP Settings
VOIP_DEFAULT_EXTENSION_LENGTH = 4
VOIP_CALL_TIMEOUT_MINUTES = 60  # Auto-close calls after 60 min

# ── Web Push (VAPID) ──────────────────────────────────────────────────────────
import os as _os
from dotenv import load_dotenv as _load
_load(BASE_DIR / '.env')

VAPID_PUBLIC_KEY  = _os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = _os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_EMAIL       = _os.environ.get('VAPID_EMAIL', 'admin@beitbridge.gov.zw')

# Expose public key to templates
PUSH_PUBLIC_KEY = VAPID_PUBLIC_KEY

# ── Django Channels (WebSocket real-time notifications) ───────────────────────
# Uses in-memory channel layer — works on LAN with no Redis required.
# For multi-server production, swap to RedisChannelLayer.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}
ASGI_APPLICATION = "nexus_helpdesk.asgi.application"

# ── Performance Optimisations ─────────────────────────────────────────────────

# In-memory cache — eliminates repeated identical DB queries within a request
# For multi-server production, swap to RedisCache or Memcached
CACHES = {
    'default': {
        'BACKEND':  'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'beitdesk-cache',
        'TIMEOUT':  300,  # 5 minutes
        'OPTIONS':  {'MAX_ENTRIES': 1000},
    }
}

# Session: store in cache for faster reads (avoids DB hit per request)
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'

# Database connection persistence — reuse DB connections across requests
# Saves ~1–3ms per request on Windows (avoids socket setup overhead)
# Set to 0 if using SQLite with concurrent writes (default)
CONN_MAX_AGE = 0  # Keep 0 for SQLite; set 60 for PostgreSQL

# Gzip compression for all responses (massive bandwidth saving on slow networks)
# WhiteNoise already compresses static files; this covers HTML responses
USE_ETAGS = True  # 304 Not Modified for unchanged pages

# Template caching — parse templates once, reuse compiled version
# Active in production (DEBUG=False). In development, templates reload on change.
# No config needed — Django does this automatically when DEBUG=False.
