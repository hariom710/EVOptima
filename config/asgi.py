"""
ASGI config for EVOptima: HTTP + WebSocket.

Merged from both former projects:

- project A provided the plain ``get_asgi_application`` entry point;
- fault2 provided the ``ProtocolTypeRouter`` / ``AuthMiddlewareStack`` /
  ``URLRouter`` WebSocket plumbing.

Run with an ASGI server, e.g. ``daphne config.asgi:application``.
"""
import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

django_asgi_app = get_asgi_application()

from apps.monitoring.routing import websocket_urlpatterns  # noqa: E402  (must follow django setup)

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # Origin check first, then session auth: without it any third-party
        # page could open /ws/monitoring/ with the visitor's cookies attached
        # (cross-site WebSocket hijacking -- the handshake is not subject to
        # the same-origin policy). AllowedHostsOriginValidator matches the
        # Origin header against settings.ALLOWED_HOSTS and denies the
        # connection when no Origin is presented.
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)
