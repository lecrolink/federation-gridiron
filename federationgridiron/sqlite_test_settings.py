"""Settings for database writer tests. The configured MySQL database is never used."""

import os

os.environ.setdefault('FG_DB_PASSWORD', 'unused-isolated-test')

from federationgridiron.settings import *  # noqa: E402,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}
