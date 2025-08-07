"""
Override path-related settings to use system wide paths.

The paths indicated in this file are those setup by the official Debian
package.
"""

__all__ = [
    'DEBUSINE_DATA_PATH',
    'DEBUSINE_CACHE_DIRECTORY',
    'DEBUSINE_LOG_DIRECTORY',
]

DEBUSINE_DATA_PATH = '/var/lib/debusine/server'
DEBUSINE_CACHE_DIRECTORY = '/var/cache/debusine/server'
DEBUSINE_LOG_DIRECTORY = '/var/log/debusine/server'
