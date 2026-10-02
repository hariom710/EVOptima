"""Shared, framework-independent helpers.

Modules here must not import Django models at import time — ``core`` is a
plain package (it is deliberately *not* in ``INSTALLED_APPS``) so it can be
imported from settings, management commands, tests and the training scripts
without pulling in the whole app registry.
"""
