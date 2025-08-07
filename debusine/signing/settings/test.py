"""Appropriate settings to run the test suite."""

from debusine.signing.settings import defaults
from debusine.signing.settings.development import *  # noqa: F401, F403

# Restore INSTALLED_APPS from defaults to disable debug_toolbar
INSTALLED_APPS = defaults.INSTALLED_APPS.copy()

# Some checks need to know if we are running tests or not
# Test run with DEBUG=False but it is allowed, for example, to use
# the default SECRET_KEY during tests
TEST_MODE = True

# When running the test suite, enable all apps so that we have all the models
INSTALLED_APPS.extend([])
