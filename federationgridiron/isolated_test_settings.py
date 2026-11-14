"""Settings for pure tests. The configured MySQL database is never used."""

import os

os.environ.setdefault('FG_SECRET_KEY', 'unused-isolated-test')
os.environ.setdefault('FG_DB_NAME', 'unused-isolated-test')
os.environ.setdefault('FG_DB_PASSWORD', 'unused-isolated-test')
os.environ.setdefault('FG_ALLOWED_HOSTS', 'testserver,localhost')

from federationgridiron.settings import *  # noqa: E402,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

TEST_RUNNER = 'federationgridiron.isolated_test_runner.IsolatedTestRunner'
