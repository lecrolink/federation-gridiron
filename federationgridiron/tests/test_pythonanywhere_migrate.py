"""The test-database migration stops unless every check passes.

These tests use temporary files and local repositories. They do not open SSH,
call the PythonAnywhere API, connect to MySQL, or read credentials.
"""

import os
import re
import stat
import subprocess
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]
MIGRATE = ROOT / 'scripts' / 'pythonanywhere_migrate_test.sh'
WORKFLOW = ROOT / '.github' / 'workflows' / 'migrate-pythonanywhere.yml'
DEPLOY_WORKFLOW = ROOT / '.github' / 'workflows' / 'deploy-pythonanywhere.yml'
PUBLISHED_FINGERPRINT = 'SHA256:zy2jmqxNg/fs6tFZK55OjHTI3B2UofzOiUvTPtcX3/Y'
TEST_DATABASE = 'lecrolink$fg_test'
PRODUCTION_DATABASE = 'lecrolink$fg'
CONFIRMATION = 'MIGRATE_FG_TEST'


def _git_env():
    return {
        'GIT_AUTHOR_NAME': 'Migrate Test',
        'GIT_AUTHOR_EMAIL': 'migrate-test@example.com',
        'GIT_COMMITTER_NAME': 'Migrate Test',
        'GIT_COMMITTER_EMAIL': 'migrate-test@example.com',
        'GIT_TERMINAL_PROMPT': '0',
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'HOME': os.environ.get('HOME', '/tmp'),
        'TMPDIR': os.environ.get('TMPDIR', '/tmp'),
    }


def _git(repo, *args, check=True):
    return subprocess.run(
        ['git', '-C', str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
        env=_git_env(),
    )


def _commit(repo, name, content):
    path = Path(repo) / name
    path.write_text(content)
    _git(repo, 'add', name)
    _git(repo, 'commit', '-m', f'Add {name}')


def _head(repo):
    return _git(repo, 'rev-parse', 'HEAD').stdout.strip()


def _executable_lines(path):
    return '\n'.join(
        line for line in Path(path).read_text().splitlines()
        if line.strip() and not line.strip().startswith('#')
    )


class PythonAnywhereMigrateScriptTests(SimpleTestCase):
    def setUp(self):
        root = Path(self._tmpdir())
        self.origin = root / 'origin'
        self.checkout = root / 'checkout'
        self.venv = root / 'venv'
        self.log = root / 'python-calls.log'
        self.origin.mkdir()
        _git(self.origin, 'init', '-b', 'main')
        _commit(self.origin, 'README', 'origin\n')
        subprocess.run(
            ['git', 'clone', str(self.origin), str(self.checkout)],
            check=True,
            capture_output=True,
            text=True,
            env=_git_env(),
        )
        _git(self.checkout, 'checkout', '-B', 'main', 'origin/main')
        self._write_fake_venv()

    def _tmpdir(self):
        path = Path(os.environ.get('TMPDIR', '/tmp')) / f'fg-migrate-{os.getpid()}-{id(self)}'
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(subprocess.run, ['rm', '-rf', str(path)], check=False)
        return path

    def _write_fake_venv(self):
        bindir = self.venv / 'bin'
        bindir.mkdir(parents=True)
        activate = bindir / 'activate'
        activate.write_text(
            'export PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd):${PATH}"\n'
        )
        python = bindir / 'python'
        python.write_text(
            '#!/bin/bash\n'
            'log="${FG_MIGRATE_LOG:?}"\n'
            'if [ "${1:-}" = "-c" ]; then\n'
            '  printf "%s\\n" "name" >> "${log}"\n'
            '  printf "%s" "${FG_FAKE_DB_NAME:-lecrolink\\$fg_test}"\n'
            '  exit 0\n'
            'fi\n'
            'printf "%s\\n" "$*" >> "${log}"\n'
            'if [ "${1:-}" = "manage.py" ] && [ "${2:-}" = "migrate" ] && [ "${3:-}" = "--plan" ]; then\n'
            '  if [ "${FG_FAKE_PLAN_STATUS:-0}" != "0" ]; then\n'
            '    echo "plan failed" >&2\n'
            '    exit 1\n'
            '  fi\n'
            '  echo "Planned operations: none"\n'
            '  exit 0\n'
            'fi\n'
            'if [ "${1:-}" = "manage.py" ] && [ "${2:-}" = "migrate" ]; then\n'
            '  echo "Applying migrations."\n'
            '  exit 0\n'
            'fi\n'
            'echo "unexpected python arguments: $*" >&2\n'
            'exit 1\n'
        )
        python.chmod(python.stat().st_mode | stat.S_IEXEC)

    def _run(self, checkout=None, **env):
        command_env = _git_env()
        command_env['FG_MIGRATE_VENV'] = str(self.venv)
        command_env['FG_MIGRATE_LOG'] = str(self.log)
        command_env['FG_FAKE_DB_NAME'] = TEST_DATABASE
        command_env.update(env)
        return subprocess.run(
            ['bash', str(MIGRATE), str(checkout or self.checkout)],
            capture_output=True,
            text=True,
            env=command_env,
            check=False,
        )

    def _calls(self):
        if not self.log.exists():
            return []
        return [line for line in self.log.read_text().splitlines() if line]

    def test_matching_main_prints_the_plan_and_then_migrates(self):
        before = _head(self.checkout)
        completed = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(_head(self.checkout), before)
        self.assertIn(f'Database name is {TEST_DATABASE}.', completed.stdout)
        self.assertIn('Migration plan:', completed.stdout)
        self.assertIn('Planned operations: none', completed.stdout)
        self.assertIn(f'Migrations applied on {TEST_DATABASE}.', completed.stdout)
        self.assertEqual(
            self._calls(),
            ['name', 'manage.py migrate --plan', 'manage.py migrate'],
        )
        self.assertLess(
            completed.stdout.index('Migration plan:'),
            completed.stdout.index('Applying migrations.'),
        )

    def test_ignored_file_does_not_block_a_clean_main(self):
        (self.origin / '.gitignore').write_text('*.pyc\n')
        _git(self.origin, 'add', '.gitignore')
        _git(self.origin, 'commit', '-m', 'Ignore bytecode')
        _git(self.checkout, 'fetch', 'origin', 'main')
        _git(self.checkout, 'merge', '--ff-only', 'origin/main')
        (self.checkout / 'cached.pyc').write_bytes(b'\x00')
        self.assertEqual(_git(self.checkout, 'status', '--porcelain').stdout, '')
        completed = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self._calls()[-1], 'manage.py migrate')

    def test_dirty_checkout_stops_before_python(self):
        before = _head(self.checkout)
        (self.checkout / 'README').write_text('local edit\n')
        completed = self._run()
        self.assertEqual(completed.returncode, 1)
        self.assertIn('dirty', completed.stderr)
        self.assertEqual(_head(self.checkout), before)
        self.assertEqual(self._calls(), [])
        self.assertNotIn('manage.py', completed.stdout)

    def test_untracked_file_stops_before_python(self):
        (self.checkout / 'local-note.txt').write_text('leave me\n')
        completed = self._run()
        self.assertEqual(completed.returncode, 1)
        self.assertIn('dirty', completed.stderr)
        self.assertEqual(self._calls(), [])

    def test_other_branch_stops_before_python(self):
        _git(self.checkout, 'checkout', '-b', 'feature')
        completed = self._run()
        self.assertEqual(completed.returncode, 1)
        self.assertIn("on 'feature', not main", completed.stderr)
        self.assertEqual(self._calls(), [])

    def test_checkout_behind_origin_main_is_not_migrated(self):
        before = _head(self.checkout)
        _commit(self.origin, 'next.txt', 'next\n')
        completed = self._run()
        self.assertEqual(completed.returncode, 1)
        self.assertIn('not origin/main', completed.stderr)
        self.assertEqual(_head(self.checkout), before)
        self.assertFalse((self.checkout / 'next.txt').exists())
        self.assertEqual(self._calls(), [])

    def test_checkout_ahead_of_origin_main_is_not_migrated(self):
        _commit(self.checkout, 'local.txt', 'local\n')
        before = _head(self.checkout)
        completed = self._run()
        self.assertEqual(completed.returncode, 1)
        self.assertIn('not origin/main', completed.stderr)
        self.assertEqual(_head(self.checkout), before)
        self.assertEqual(self._calls(), [])

    def test_missing_checkout_is_refused(self):
        completed = self._run(self.checkout / 'missing')
        self.assertEqual(completed.returncode, 1)
        self.assertIn('not a git checkout', completed.stderr)
        self.assertEqual(self._calls(), [])

    def test_missing_virtualenv_stops_before_python(self):
        completed = self._run(FG_MIGRATE_VENV=str(self.checkout / 'no-venv'))
        self.assertEqual(completed.returncode, 1)
        self.assertIn('not a virtualenv', completed.stderr)
        self.assertEqual(self._calls(), [])

    def test_python_outside_the_virtualenv_is_refused(self):
        activate = self.venv / 'bin' / 'activate'
        activate.write_text('export PATH="/usr/bin:/bin"\n')
        completed = self._run()
        self.assertEqual(completed.returncode, 1)
        self.assertIn('python is not', completed.stderr)
        self.assertEqual(self._calls(), [])

    def test_production_database_name_is_refused_before_migrate(self):
        completed = self._run(FG_FAKE_DB_NAME=PRODUCTION_DATABASE)
        self.assertEqual(completed.returncode, 1)
        self.assertIn(f'is not {TEST_DATABASE}', completed.stderr)
        leaked_name = re.compile(r'lecrolink\$fg(?!_test)')
        self.assertIsNone(leaked_name.search(completed.stdout))
        self.assertIsNone(leaked_name.search(completed.stderr))
        self.assertEqual(self._calls(), ['name'])

    def test_any_other_database_name_is_refused_before_migrate(self):
        completed = self._run(FG_FAKE_DB_NAME='lecrolink$fg_test_copy')
        self.assertEqual(completed.returncode, 1)
        self.assertIn(f'is not {TEST_DATABASE}', completed.stderr)
        self.assertEqual(self._calls(), ['name'])

    def test_failed_plan_does_not_run_migrate(self):
        completed = self._run(FG_FAKE_PLAN_STATUS='1')
        self.assertEqual(completed.returncode, 1)
        self.assertIn('plan failed', completed.stderr)
        self.assertEqual(self._calls(), ['name', 'manage.py migrate --plan'])

    def test_script_only_plans_and_migrates_the_test_database(self):
        commands = _executable_lines(MIGRATE)
        self.assertIn("expected_db='lecrolink$fg_test'", commands)
        self.assertIn('/home/lecrolink/.virtualenvs/fgvenv', commands)
        self.assertIn('python manage.py migrate --plan', commands)
        self.assertIn('python manage.py migrate', commands)
        self.assertLess(
            commands.index('python manage.py migrate --plan'),
            commands.index('python manage.py migrate\n'),
        )
        self.assertLess(
            commands.index('is not ${expected_db}'),
            commands.index('python manage.py migrate --plan'),
        )
        self.assertLess(
            commands.index('origin/main'),
            commands.index('python manage.py migrate --plan'),
        )
        for forbidden in (
            'git merge',
            'git pull',
            'git reset',
            'flush',
            'loaddata',
            'dumpdata',
            'sqlflush',
            'dbshell',
            'seed',
            '--fake',
            'reload',
            'API_TOKEN',
            'SECRET',
            'PASSWORD',
        ):
            self.assertNotIn(forbidden, commands)
        without_test_name = commands.replace(TEST_DATABASE, '')
        self.assertNotIn(PRODUCTION_DATABASE, without_test_name)


class PythonAnywhereMigrateWorkflowTests(SimpleTestCase):
    def test_workflow_is_manual_and_checks_main_before_connecting(self):
        source = WORKFLOW.read_text()
        deploy = DEPLOY_WORKFLOW.read_text()
        self.assertIn('workflow_dispatch:', source)
        self.assertNotIn('push:', source)
        self.assertNotIn('pull_request', source)
        self.assertIn(f'Type {CONFIRMATION}', source)
        self.assertIn(f'!= "{CONFIRMATION}"', source)
        self.assertIn('required: true', source)
        self.assertIn('cancel-in-progress: false', source)
        self.assertIn('pythonanywhere-www-federationgridiron-com', source)
        self.assertIn("github.ref == 'refs/heads/main'", source)
        self.assertIn("github.ref != 'refs/heads/main'", source)
        self.assertIn('secrets.PYTHONANYWHERE_SSH_PRIVATE_KEY', source)
        self.assertIn('secrets.PYTHONANYWHERE_API_TOKEN', source)
        self.assertIn(PUBLISHED_FINGERPRINT, source)
        self.assertIn(PUBLISHED_FINGERPRINT, deploy)
        self.assertIn('scripts/pythonanywhere_known_host.sh', source)
        self.assertIn('scripts/pythonanywhere_migrate_test.sh', source)
        self.assertIn('/home/lecrolink/federationgridiron', source)
        self.assertIn(
            'https://www.pythonanywhere.com/api/v0/user/lecrolink/webapps/www.federationgridiron.com/reload/',
            source,
        )
        self.assertIn('if: success()', source)
        migrate_step = source.split('name: Migrate lecrolink$fg_test', 1)[1]
        migrate_step = migrate_step.split('name: Reload www.federationgridiron.com', 1)[0]
        self.assertLess(migrate_step.index(CONFIRMATION), migrate_step.index('ssh-keyscan'))
        self.assertLess(
            source.index('name: Migrate lecrolink$fg_test'),
            source.index('name: Reload www.federationgridiron.com'),
        )
        require_main = source.split('require-main:', 1)[1].split('migrate:', 1)[0]
        self.assertNotIn('ssh', require_main)
        for forbidden in (
            'flush',
            'loaddata',
            'dumpdata',
            'sqlflush',
            'dbshell',
            'seed',
            '--fake',
            'BEGIN OPENSSH',
            'BEGIN RSA',
        ):
            self.assertNotIn(forbidden, source)
        without_test_name = source.replace(TEST_DATABASE, '')
        self.assertNotIn(PRODUCTION_DATABASE, without_test_name)
