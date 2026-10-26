"""
BeitDesk ASGI configuration.
Handles both HTTP (Django) and WebSocket (Channels) connections.

Run with daphne for production:
    daphne -b 0.0.0.0 -p 8000 nexus_helpdesk.asgi:application

Or with uvicorn:
    uvicorn nexus_helpdesk.asgi:application --host 0.0.0.0 --port 8000
"""
import os
import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nexus_helpdesk.settings')
django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator
import helpdesk.routing

application = ProtocolTypeRouter({
    # Standard Django HTTP requests
    "http": get_asgi_application(),

    # WebSocket connections — authenticated, host-validated
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(helpdesk.routing.websocket_urlpatterns)
        )
    ),
})
