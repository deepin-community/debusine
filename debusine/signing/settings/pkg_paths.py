"""
Override path-related settings to use system wide paths.

The paths indicated in this file are those setup by the official Debian
package.
"""

__all__ = [
    'DEBUSINE_DATA_PATH',
    'DEBUSINE_LOG_DIRECTORY',
]

DEBUSINE_DATA_PATH = '/var/lib/debusine/signing'
DEBUSINE_LOG_DIRECTORY = '/var/log/debusine/signing'
