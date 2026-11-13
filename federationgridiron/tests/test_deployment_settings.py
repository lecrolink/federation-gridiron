"""Deployment configuration is read from the environment.

These tests use fake values and an in-memory SQLite override. They do not
open a MySQL connection.
"""

import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from federationgridiron.settings import (
    allowed_hosts_from_env,
    debug_from_env,
    load_deployment,
    require_setting,
)


ROOT = Path(__file__).resolve().parents[2]
FAKE_PASSWORD = 'unused-isolated-test'
FAKE_SECRET = 'unused-isolated-test'
TEST_DB_NAME = 'lecrolink$fg_test'


def _child_env(**extra):
    env = {
        'PATH': os.environ.get('PATH', ''),
        'HOME': os.environ.get('HOME', ''),
        'LANG': os.environ.get('LANG', 'C.UTF-8'),
    }
    pythonpath = os.environ.get('PYTHONPATH')
    if pythonpath:
        env['PYTHONPATH'] = pythonpath
    env.update(extra)
    return env


def _run(script, **env):
    completed = subprocess.run(
        [sys.executable, '-c', script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=_child_env(**env),
    )
    return completed


BLOCK_NETWORK = """
import socket
def blocked(*args, **kwargs):
    raise AssertionError('Outbound connections are blocked')
socket.socket.connect = blocked
socket.create_connection = blocked
"""


class DeploymentConfigTests(SimpleTestCase):
    def test_required_values_refuse_missing_and_blank(self):
        with self.assertRaises(ImproperlyConfigured):
            require_setting({}, 'FG_SECRET_KEY')
        with self.assertRaises(ImproperlyConfigured):
            require_setting({'FG_DB_NAME': ''}, 'FG_DB_NAME', strip=True)
        with self.assertRaises(ImproperlyConfigured):
            require_setting({'FG_DB_NAME': '   '}, 'FG_DB_NAME', strip=True)
        self.assertEqual(
            require_setting({'FG_DB_NAME': f'  {TEST_DB_NAME}  '}, 'FG_DB_NAME', strip=True),
            TEST_DB_NAME,
        )
        self.assertEqual(
            require_setting({'FG_SECRET_KEY': FAKE_SECRET}, 'FG_SECRET_KEY'),
            FAKE_SECRET,
        )

    def test_debug_defaults_to_false(self):
        self.assertFalse(debug_from_env(None))
        self.assertFalse(debug_from_env(''))
        self.assertFalse(debug_from_env('false'))
        self.assertFalse(debug_from_env('0'))
        self.assertFalse(debug_from_env('no'))
        self.assertTrue(debug_from_env('true'))
        self.assertTrue(debug_from_env('TRUE'))
        self.assertTrue(debug_from_env('1'))
        self.assertTrue(debug_from_env('yes'))
        self.assertTrue(debug_from_env('on'))

    def test_allowed_hosts_parse_a_comma_separated_list(self):
        self.assertEqual(allowed_hosts_from_env(None), [])
        self.assertEqual(allowed_hosts_from_env(''), [])
        self.assertEqual(
            allowed_hosts_from_env(
                ' federationgridiron.com, www.federationgridiron.com, ,'
            ),
            ['federationgridiron.com', 'www.federationgridiron.com'],
        )

    def test_load_deployment_has_no_database_default(self):
        environ = {
            'FG_SECRET_KEY': FAKE_SECRET,
            'FG_DB_PASSWORD': FAKE_PASSWORD,
            'FG_ALLOWED_HOSTS': 'federationgridiron.com, www.federationgridiron.com',
        }
        with self.assertRaisesRegex(ImproperlyConfigured, 'FG_DB_NAME'):
            load_deployment(environ)
        environ['FG_DB_NAME'] = f'  {TEST_DB_NAME}  '
        loaded = load_deployment(environ)
        self.assertEqual(loaded['DB_NAME'], TEST_DB_NAME)
        self.assertEqual(loaded['DB_PASSWORD'], FAKE_PASSWORD)
        self.assertEqual(loaded['SECRET_KEY'], FAKE_SECRET)
        self.assertFalse(loaded['DEBUG'])
        self.assertEqual(
            loaded['ALLOWED_HOSTS'],
            ['federationgridiron.com', 'www.federationgridiron.com'],
        )

    def test_load_deployment_requires_secret_password_and_hosts(self):
        base = {
            'FG_SECRET_KEY': FAKE_SECRET,
            'FG_DB_NAME': TEST_DB_NAME,
            'FG_DB_PASSWORD': FAKE_PASSWORD,
            'FG_ALLOWED_HOSTS': 'federationgridiron.com,www.federationgridiron.com',
        }
        for name in ('FG_SECRET_KEY', 'FG_DB_PASSWORD', 'FG_ALLOWED_HOSTS'):
            environ = dict(base)
            environ.pop(name)
            with self.assertRaisesRegex(ImproperlyConfigured, name):
                load_deployment(environ)
        turned_on = dict(base)
        turned_on['FG_DEBUG'] = 'yes'
        self.assertTrue(load_deployment(turned_on)['DEBUG'])

    def test_settings_source_does_not_hard_code_the_production_database(self):
        source = (ROOT / 'federationgridiron' / 'settings.py').read_text()
        self.assertNotIn('"lecrolink$fg"', source)
        self.assertNotIn("'lecrolink$fg'", source)
        self.assertNotIn('django-insecure-', source)

    def test_import_uses_environment_and_does_not_connect(self):
        completed = _run(
            BLOCK_NETWORK
            + """
import os
import federationgridiron.settings as configured
assert configured.DEBUG is False
assert configured.ALLOWED_HOSTS == [
    'federationgridiron.com',
    'www.federationgridiron.com',
]
database = configured.DATABASES['default']
assert database['ENGINE'] == 'django.db.backends.mysql'
assert database['NAME'] == os.environ['FG_DB_NAME']
assert database['USER'] == 'lecrolink'
assert database['HOST'] == 'lecrolink.mysql.pythonanywhere-services.com'
assert database['PASSWORD'] == os.environ['FG_DB_PASSWORD']
assert database['NAME'] != 'lecrolink$fg'
print('configured-ok')
""",
            FG_SECRET_KEY=FAKE_SECRET,
            FG_DB_NAME=TEST_DB_NAME,
            FG_DB_PASSWORD=FAKE_PASSWORD,
            FG_ALLOWED_HOSTS='federationgridiron.com, www.federationgridiron.com',
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('configured-ok', completed.stdout)

    def test_import_refuses_a_missing_secret_without_connecting(self):
        completed = _run(
            BLOCK_NETWORK
            + """
try:
    import federationgridiron.settings
except Exception as exc:
    print(type(exc).__name__)
    print(exc)
else:
    raise SystemExit('settings imported without FG_SECRET_KEY')
""",
            FG_DB_NAME=TEST_DB_NAME,
            FG_DB_PASSWORD=FAKE_PASSWORD,
            FG_ALLOWED_HOSTS='federationgridiron.com',
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('ImproperlyConfigured', completed.stdout)
        self.assertIn('FG_SECRET_KEY', completed.stdout)

    def test_import_refuses_a_missing_database_name_without_connecting(self):
        completed = _run(
            BLOCK_NETWORK
            + """
try:
    import federationgridiron.settings
except Exception as exc:
    print(type(exc).__name__)
    print(exc)
else:
    raise SystemExit('settings imported without FG_DB_NAME')
""",
            FG_SECRET_KEY=FAKE_SECRET,
            FG_DB_PASSWORD=FAKE_PASSWORD,
            FG_ALLOWED_HOSTS='www.federationgridiron.com',
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('ImproperlyConfigured', completed.stdout)
        self.assertIn('FG_DB_NAME', completed.stdout)

    def test_sqlite_test_settings_need_no_real_credentials(self):
        completed = _run(
            BLOCK_NETWORK
            + """
import federationgridiron.sqlite_test_settings as test_settings
import federationgridiron.isolated_test_settings as isolated_settings
for module in (test_settings, isolated_settings):
    database = module.DATABASES['default']
    assert database['ENGINE'] == 'django.db.backends.sqlite3'
    assert database['NAME'] == ':memory:'
print('sqlite-ok')
"""
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('sqlite-ok', completed.stdout)

    def test_running_suite_is_on_sqlite(self):
        database = settings.DATABASES['default']
        self.assertEqual(database['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(database['NAME'], ':memory:')
