"""
The settings in this file are tailored overrides for running in production.

When running in production, selected.py should point to this file.
"""

# PostgreSQL should be used in production
try:
    from debusine.signing.settings.db_postgresql import DATABASES  # noqa: F401
except (ModuleNotFoundError, PermissionError):
    from debusine.signing.settings.db_postgresql_default import (  # noqa: F401
        DATABASES,
    )

# Use paths from the package
from debusine.signing.settings.pkg_paths import *  # noqa: F401, F403, I202
