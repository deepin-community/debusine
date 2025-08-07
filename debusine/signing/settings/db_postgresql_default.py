"""
PostgreSQL settings.

Defaults to unix socket with user auth.
"""

import getpass

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'debusine-signing',
        'USER': getpass.getuser(),
        'PASSWORD': '',
        'HOST': '',
        'PORT': '',
        'TEST': {'NAME': 'debusine-test-signing'},
        'ATOMIC_REQUESTS': True,
    }
}
