"""
BeitDesk HTTPS Server
======================
Runs BeitDesk over HTTPS so other devices on your LAN can install it as a PWA.

REQUIREMENTS
------------
1. Install werkzeug:
       pip install werkzeug

2. Generate mkcert certificates (run once):
       mkcert -install
       mkcert 192.168.1.171 localhost 127.0.0.1
   This creates:  192.168.1.171+2.pem  and  192.168.1.171+2-key.pem
   Replace 192.168.1.171 with your actual LAN IP (from ipconfig).

3. Update CERT and KEY paths below to match your certificate filenames.

4. Run:
       python run_https.py

5. Other devices on your network open:
       https://192.168.1.171:8000
   Then install BeitDesk as a PWA from their browser menu.

NOTES
-----
- This replaces 'py manage.py runserver' for HTTPS use.
- For normal HTTP (no PWA on other devices): py manage.py runserver 0.0.0.0:8000
- Make sure your LAN IP in settings.py CSRF_TRUSTED_ORIGINS matches.
"""

import os
import sys

# ── Certificate paths — update these to match your mkcert output filenames ───
CERT = '192.168.1.171+2.pem'       # Your .pem certificate file
KEY  = '192.168.1.171+2-key.pem'   # Your .pem key file

# ── Port — change if needed ───────────────────────────────────────────────────
PORT = 8000
HOST = '0.0.0.0'   # Listen on all interfaces so LAN devices can connect

# ─────────────────────────────────────────────────────────────────────────────

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nexus_helpdesk.settings')

# Verify certificate files exist before starting
if not os.path.exists(CERT):
    print(f'\n❌  Certificate not found: {CERT}')
    print('    Run mkcert first:')
    print(f'    mkcert -install')
    print(f'    mkcert 192.168.1.171 localhost 127.0.0.1')
    print('    Then update CERT and KEY paths at the top of this file.\n')
    sys.exit(1)

if not os.path.exists(KEY):
    print(f'\n❌  Key file not found: {KEY}')
    print('    Check the KEY path at the top of this file.\n')
    sys.exit(1)

try:
    from werkzeug.serving import run_simple
except ImportError:
    print('\n❌  werkzeug not installed.')
    print('    Run:  pip install werkzeug\n')
    sys.exit(1)

try:
    import django
    django.setup()
    from django.core.wsgi import get_wsgi_application
    application = get_wsgi_application()
except Exception as e:
    print(f'\n❌  Django setup failed: {e}\n')
    sys.exit(1)

import socket
try:
    lan_ip = socket.gethostbyname(socket.gethostname())
except Exception:
    lan_ip = '(your LAN IP)'

print()
print('=' * 60)
print('  BeitDesk — HTTPS Server')
print('  Municipality of Beitbridge IT Help Desk')
print('=' * 60)
print(f'  Local:    https://localhost:{PORT}')
print(f'  Network:  https://{lan_ip}:{PORT}')
print(f'  Cert:     {CERT}')
print()
print('  Other devices on your LAN:')
print(f'  → Open https://{lan_ip}:{PORT} in Chrome/Edge')
print('  → Click the install button to add BeitDesk to home screen')
print()
print('  Press CTRL+C to stop')
print('=' * 60)
print()

run_simple(
    HOST,
    PORT,
    application,
    ssl_context=(CERT, KEY),
    use_reloader=True,
    use_debugger=False,   # Keep False — Django's own debug handles errors
    threaded=True,
)
