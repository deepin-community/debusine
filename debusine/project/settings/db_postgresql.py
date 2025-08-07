"""
PostgreSQL settings.

Defaults to unix socket with user auth.
"""

import getpass

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'debusine',
        'USER': getpass.getuser(),
        'PASSWORD': '',
        'HOST': '',
        'PORT': '',
        'TEST': {'NAME': 'debusine-test'},
        'ATOMIC_REQUESTS': True,
    }
}
