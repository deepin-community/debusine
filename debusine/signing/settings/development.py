"""
Appropriate settings to run during development.

When running in development mode, selected.py should point to this file.
"""

from debusine.signing.settings import defaults

try:
    from debusine.signing.settings.db_postgresql import DATABASES
except (ModuleNotFoundError, PermissionError):
    from debusine.signing.settings.db_postgresql_default import DATABASES

__all__ = [
    'DATABASES',
    'DEBUG',
    'INSTALLED_APPS',
]

DEBUG = True

INSTALLED_APPS = defaults.INSTALLED_APPS.copy()
# INSTALLED_APPS.append('debug_toolbar')
