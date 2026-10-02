"""Project configuration package for EVOptima.

- :mod:`config.settings` — settings package (``base`` → ``dev`` / ``prod``)
- :mod:`config.urls`      — root URL configuration
- :mod:`config.asgi`      — ASGI entry point (HTTP + WebSocket)
- :mod:`config.wsgi`      — WSGI entry point (HTTP only)

``DJANGO_SETTINGS_MODULE`` selects the active settings file; ``manage.py``,
``asgi.py`` and ``wsgi.py`` all default to ``config.settings.dev``.
"""
