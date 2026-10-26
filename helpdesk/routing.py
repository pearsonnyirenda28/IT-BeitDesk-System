from django.urls import re_path
from helpdesk import consumers

websocket_urlpatterns = [
    # ws://host/ws/notifications/  — one connection per logged-in user
    re_path(r'^ws/notifications/$', consumers.NotificationConsumer.as_asgi()),
]
