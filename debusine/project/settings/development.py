"""
Appropriate settings to run during development.

When running in development mode, selected.py should point to this file.
"""

from debusine.project.settings import defaults

try:
    from debusine.project.settings.db_postgresql import DATABASES
except (ModuleNotFoundError, PermissionError):
    from debusine.project.settings.db_postgresql_default import DATABASES

__all__ = [
    'ADMINS',
    'CACHES',
    'DATABASES',
    'DEBUG',
    'EMAIL_BACKEND',
    'INSTALLED_APPS',
    'INTERNAL_IPS',
    'MIDDLEWARE',
    'SITE_URL',
    'XHR_SIMULATED_DELAY',
]

DEBUG = True

ADMINS = ()

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

SITE_URL = 'http://127.0.0.1:8000'

CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
}

XHR_SIMULATED_DELAY = 0.5

INSTALLED_APPS = defaults.INSTALLED_APPS.copy()
# INSTALLED_APPS.append('debug_toolbar')

MIDDLEWARE = defaults.MIDDLEWARE.copy()
# MIDDLEWARE.insert(0, 'debug_toolbar.middleware.DebugToolbarMiddleware')

INTERNAL_IPS = ['127.0.0.1']
