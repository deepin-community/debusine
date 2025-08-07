"""Appropriate settings to run the test suite."""

from debusine.project.settings import defaults
from debusine.project.settings.development import *  # noqa: F401, F403

# Don't use bcrypt to run tests (speed gain)
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Restore INSTALLED_APPS and MIDDLEWARE from defaults to disable debug_toolbar
INSTALLED_APPS = defaults.INSTALLED_APPS.copy()
MIDDLEWARE = defaults.MIDDLEWARE

TEST_NON_SERIALIZED_APPS = ['django.contrib.contenttypes']

# Some checks need to know if we are running tests or not
# Test run with DEBUG=False but it is allowed, for example, to use
# the default SECRET_KEY during tests
TEST_MODE = True

# In normal operation of Debusine, when a Worker / WorkRequest changes,
# it will try to schedule the execution.
# In order to be able to call the scheduler explicitly from the unit tests,
# the automatic scheduling can be disabled. This is only for unit tests
# that need to call the scheduler and verify the results.
DISABLE_AUTOMATIC_SCHEDULING = False

# When running the test suite, enable all apps so that we have all the models
INSTALLED_APPS.extend([])

# Ensure FQDN is an FQDN for use in email addresses
if "." not in defaults.DEBUSINE_FQDN:
    DEBUSINE_FQDN = f"{defaults.DEBUSINE_FQDN}.example.net"

# InMemoryChannelLayer is faster and more reliable in unit tests, but
# unsuitable for production use since it can't communicate between
# processes.
CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}
