"""The test-world workflow stops unless every check passes.

These tests use temporary files and local repositories. They do not open SSH,
call the PythonAnywhere API, connect to MySQL, or read credentials.
"""

import os
import stat
import subprocess
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / 'scripts' / 'pythonanywhere_world.sh'
PREFLIGHT = ROOT / 'scripts' / 'pythonanywhere_world_preflight.sh'
WORKFLOW = ROOT / '.github' / 'workflows' / 'build-pythonanywhere-world.yml'
DEPLOY_WORKFLOW = ROOT / '.github' / 'workflows' / 'deploy-pythonanywhere.yml'
MIGRATE_WORKFLOW = ROOT / '.github' / 'workflows' / 'migrate-pythonanywhere.yml'
PUBLISHED_FINGERPRINT = 'SHA256:zy2jmqxNg/fs6tFZK55OjHTI3B2UofzOiUvTPtcX3/Y'
TEST_DATABASE = 'lecrolink$fg_test'
PRODUCTION_DATABASE = 'lecrolink$fg'
STAGES = ('VERIFY', 'BUILD_DRAFT', 'PUBLISH', 'START_1970')


def _git_env():
    return {
        'GIT_AUTHOR_NAME': 'World Test',
        'GIT_AUTHOR_EMAIL': 'world-test@example.com',
        'GIT_COMMITTER_NAME': 'World Test',
        'GIT_COMMITTER_EMAIL': 'world-test@example.com',
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


class PythonAnywhereWorldScriptTests(SimpleTestCase):
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
        self._tmp_n = getattr(self, '_tmp_n', 0) + 1
        path = Path(os.environ.get('TMPDIR', '/tmp')) / (
            f'fg-world-{os.getpid()}-{id(self)}-{self._tmp_n}'
        )
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
            'log="${FG_WORLD_LOG:?}"\n'
            'if [ "${1:-}" = "-c" ]; then\n'
            '  printf "%s\\n" "name" >> "${log}"\n'
            '  printf "%s" "${FG_FAKE_DB_NAME:-lecrolink\\$fg_test}"\n'
            '  exit 0\n'
            'fi\n'
            'printf "%s\\n" "$*" >> "${log}"\n'
            'if [ "${1:-}" = "manage.py" ] && [ "${2:-}" = "build_fg_test_world" ] \\\n'
            '  && [ "${3:-}" = "--stage" ]; then\n'
            '  case "${4:-}" in\n'
            '    VERIFY|BUILD_DRAFT|PUBLISH|START_1970)\n'
            '      printf "world_result=%s\\n" "${FG_WORLD_RESULT:-readonly}"\n'
            '      exit 0\n'
            '      ;;\n'
            '  esac\n'
            'fi\n'
            'echo "unexpected python arguments: $*" >&2\n'
            'exit 1\n'
        )
        python.chmod(python.stat().st_mode | stat.S_IEXEC)

    def _run(self, checkout=None, stage='VERIFY', **env):
        command_env = _git_env()
        command_env['FG_WORLD_VENV'] = str(self.venv)
        command_env['FG_WORLD_LOG'] = str(self.log)
        command_env['FG_FAKE_DB_NAME'] = TEST_DATABASE
        command_env.update(env)
        return subprocess.run(
            ['bash', str(WORLD), str(checkout or self.checkout), stage],
            capture_output=True,
            text=True,
            env=command_env,
            check=False,
        )

    def _calls(self):
        if not self.log.exists():
            return []
        return [line for line in self.log.read_text().splitlines() if line]

    def test_each_stage_reaches_the_command_on_a_clean_main(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                if self.log.exists():
                    self.log.unlink()
                completed = self._run(stage=stage, FG_WORLD_RESULT='readonly')
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn(f'Database name is {TEST_DATABASE}.', completed.stdout)
                self.assertIn('world_result=readonly', completed.stdout)
                self.assertEqual(
                    self._calls(),
                    ['name', f'manage.py build_fg_test_world --stage {stage}'],
                )

    def test_dirty_wrong_branch_and_mismatched_head_stop_before_python(self):
        before = _head(self.checkout)
        (self.checkout / 'README').write_text('local edit\n')
        dirty = self._run()
        self.assertEqual(dirty.returncode, 1)
        self.assertIn('dirty', dirty.stderr)
        self.assertEqual(self._calls(), [])
        (self.checkout / 'README').write_text('origin\n')
        _git(self.checkout, 'checkout', '-b', 'feature')
        branched = self._run()
        self.assertEqual(branched.returncode, 1)
        self.assertIn("on 'feature', not main", branched.stderr)
        self.assertEqual(self._calls(), [])
        _git(self.checkout, 'checkout', 'main')
        _commit(self.origin, 'next.txt', 'next\n')
        behind = self._run()
        self.assertEqual(behind.returncode, 1)
        self.assertIn('not origin/main', behind.stderr)
        self.assertEqual(_head(self.checkout), before)
        self.assertFalse((self.checkout / 'next.txt').exists())
        self.assertEqual(self._calls(), [])

    def test_checkout_ahead_of_origin_main_is_refused(self):
        _commit(self.checkout, 'local.txt', 'local\n')
        completed = self._run()
        self.assertEqual(completed.returncode, 1)
        self.assertIn('not origin/main', completed.stderr)
        self.assertEqual(self._calls(), [])

    def test_dollar_in_the_database_name_is_not_expanded(self):
        completed = self._run(fg_test='EXPANDED', FG_FAKE_DB_NAME=TEST_DATABASE)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(f'Database name is {TEST_DATABASE}.', completed.stdout)
        self.assertNotIn('EXPANDED', completed.stdout)
        self.assertNotIn('EXPANDED', completed.stderr)
        source = WORLD.read_text()
        self.assertIn("expected_db='lecrolink$fg_test'", source)
        self.assertIn("production_db='lecrolink$fg'", source)
        self.assertNotIn('expected_db="lecrolink$fg_test"', source)
        self.assertNotIn('expected_db=lecrolink$fg_test', source)
        self.assertNotIn('production_db="lecrolink$fg"', source)
        self.assertNotIn('production_db=lecrolink$fg', source)

    def test_production_database_name_is_refused_before_the_command(self):
        completed = self._run(FG_FAKE_DB_NAME=PRODUCTION_DATABASE)
        self.assertEqual(completed.returncode, 1)
        self.assertIn('production database is not a target', completed.stderr)
        self.assertNotIn(PRODUCTION_DATABASE, completed.stdout)
        self.assertNotIn(PRODUCTION_DATABASE, completed.stderr)
        self.assertEqual(self._calls(), ['name'])

    def test_any_other_database_name_is_refused_before_the_command(self):
        completed = self._run(FG_FAKE_DB_NAME='lecrolink$fg_test_copy')
        self.assertEqual(completed.returncode, 1)
        self.assertIn(f'is not {TEST_DATABASE}', completed.stderr)
        self.assertEqual(self._calls(), ['name'])

    def test_illegal_stage_stops_before_git_or_python(self):
        completed = self._run(stage='VERIFY;touch pwned')
        self.assertEqual(completed.returncode, 1)
        self.assertIn('stage is not legal', completed.stderr)
        self.assertNotIn('VERIFY;touch pwned', completed.stdout)
        self.assertNotIn('VERIFY;touch pwned', completed.stderr)
        self.assertFalse((self.checkout / 'pwned').exists())
        self.assertEqual(self._calls(), [])

    def test_script_only_runs_the_world_command(self):
        commands = _executable_lines(WORLD)
        syntax = subprocess.run(['bash', '-n', str(WORLD)], check=False)
        self.assertEqual(syntax.returncode, 0)
        self.assertIn("expected_db='lecrolink$fg_test'", commands)
        self.assertIn("production_db='lecrolink$fg'", commands)
        self.assertIn('/home/lecrolink/.virtualenvs/fgvenv', commands)
        self.assertIn('python manage.py build_fg_test_world --stage "${stage}"', commands)
        self.assertLess(
            commands.index('production database is not a target'),
            commands.index('python manage.py build_fg_test_world'),
        )
        self.assertLess(
            commands.index('origin/main'),
            commands.index('python manage.py build_fg_test_world'),
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
            'migrate',
            '--fake',
            'reload',
            'API_TOKEN',
            'SECRET',
            'PASSWORD',
            'world_result=writes',
        ):
            self.assertNotIn(forbidden, commands)


class PythonAnywhereWorldWorkflowTests(SimpleTestCase):
    def test_workflow_is_manual_and_shares_the_server_lock(self):
        source = WORKFLOW.read_text()
        deploy = DEPLOY_WORKFLOW.read_text()
        migrate = MIGRATE_WORKFLOW.read_text()
        self.assertIn('name: Build PythonAnywhere test world', source)
        self.assertIn('workflow_dispatch:', source)
        self.assertNotIn('push:', source)
        self.assertNotIn('pull_request', source)
        self.assertIn('type: choice', source)
        for stage in STAGES:
            self.assertIn(f'- {stage}', source)
        self.assertIn('confirmation:', source)
        self.assertIn('type: string', source)
        self.assertIn('STAGE: ${{ inputs.stage }}', source)
        self.assertIn('CONFIRMATION: ${{ inputs.confirmation }}', source)
        self.assertEqual(source.count('bash scripts/pythonanywhere_world_preflight.sh'), 2)
        self.assertIn('cancel-in-progress: false', source)
        self.assertIn('cancel-in-progress: false', deploy)
        self.assertIn('cancel-in-progress: false', migrate)
        self.assertIn('pythonanywhere-www-federationgridiron-com', source)
        self.assertIn('pythonanywhere-www-federationgridiron-com', deploy)
        self.assertIn('pythonanywhere-www-federationgridiron-com', migrate)
        self.assertIn("github.ref == 'refs/heads/main'", source)
        self.assertIn("github.ref != 'refs/heads/main'", source)
        self.assertIn('timeout-minutes: 180', source)
        self.assertIn('secrets.PYTHONANYWHERE_SSH_PRIVATE_KEY', source)
        self.assertIn('secrets.PYTHONANYWHERE_API_TOKEN', source)
        self.assertIn(PUBLISHED_FINGERPRINT, source)
        self.assertIn('scripts/pythonanywhere_known_host.sh', source)
        self.assertIn('scripts/pythonanywhere_world.sh', source)
        self.assertIn('/home/lecrolink/federationgridiron', source)
        self.assertIn("grep -qx 'world_result=writes'", source)
        self.assertIn("steps.build.outputs.write == 'yes'", source)
        self.assertIn('if: success() && steps.build.outputs.write == \'yes\'', source)
        self.assertNotIn('grep created', source)
        self.assertNotIn('grep -E', source)
        self.assertIn(
            'https://www.pythonanywhere.com/api/v0/user/lecrolink/webapps/www.federationgridiron.com/reload/',
            source,
        )
        build_job = source.split('\n  build:', 1)[1]
        self.assertIn('timeout-minutes: 180', build_job)
        self.assertLess(
            build_job.index('bash scripts/pythonanywhere_world_preflight.sh'),
            build_job.index('secrets.PYTHONANYWHERE_SSH_PRIVATE_KEY'),
        )
        self.assertLess(
            build_job.index('bash scripts/pythonanywhere_world_preflight.sh'),
            build_job.index('ssh-keyscan'),
        )
        build_step = build_job.split('name: Build the test world', 1)[1].split(
            'name: Reload www.federationgridiron.com',
            1,
        )[0]
        self.assertNotIn('inputs.confirmation', build_step)
        self.assertNotIn('CONFIRMATION', build_step)
        self.assertNotIn('API_TOKEN', build_step)
        self.assertNotIn('PYTHONANYWHERE_API_TOKEN', build_step)
        require_main = source.split('require-main:', 1)[1].split('\n  build:', 1)[0]
        self.assertNotIn('timeout-minutes: 180', require_main)
        self.assertNotIn('ssh-keyscan', require_main)
        self.assertNotIn('ssh ', require_main)
        self.assertNotIn('pythonanywhere.com', require_main)
        self.assertNotIn('API_TOKEN', require_main)
        for forbidden in (
            'flush',
            'loaddata',
            'dumpdata',
            'sqlflush',
            'dbshell',
            'migrate',
            '--fake',
            'BEGIN OPENSSH',
            'BEGIN RSA',
        ):
            self.assertNotIn(forbidden, source)


class PythonAnywhereWorldPreflightTests(SimpleTestCase):
    def _preflight(self, stage='VERIFY', confirmation=None, ref='refs/heads/main', include_confirmation=True, include_ref=True, include_stage=True):
        if confirmation is None:
            confirmation = stage
        bindir = Path(self._tmpdir()) / 'bin'
        bindir.mkdir()
        ssh_log = bindir.parent / 'ssh.log'
        ssh = bindir / 'ssh'
        ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "${SSH_LOG}"\nexit 99\n')
        ssh.chmod(ssh.stat().st_mode | stat.S_IEXEC)
        keyscan = bindir / 'ssh-keyscan'
        keyscan.write_text(ssh.read_text())
        keyscan.chmod(keyscan.stat().st_mode | stat.S_IEXEC)
        env = {
            'PATH': f'{bindir}:/usr/bin:/bin',
            'HOME': '/tmp',
            'SSH_LOG': str(ssh_log),
        }
        if include_stage:
            env['STAGE'] = stage
        if include_confirmation:
            env['CONFIRMATION'] = confirmation
        if include_ref:
            env['GITHUB_REF'] = ref
        completed = subprocess.run(
            ['bash', str(PREFLIGHT)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        completed.ssh_log = ssh_log.read_text() if ssh_log.exists() else ''
        return completed

    def _tmpdir(self):
        self._tmp_n = getattr(self, '_tmp_n', 0) + 1
        path = Path(os.environ.get('TMPDIR', '/tmp')) / (
            f'fg-world-preflight-{os.getpid()}-{id(self)}-{self._tmp_n}'
        )
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(subprocess.run, ['rm', '-rf', str(path)], check=False)
        return path

    def test_every_stage_and_surrounding_spaces_pass(self):
        for stage in STAGES:
            accepted = (
                stage,
                ' ' + stage,
                '    ' + stage,
                stage + ' ',
                ' ' + stage + ' ',
                '        ' + stage + '   ',
            )
            for value in accepted:
                with self.subTest(stage=stage, value=repr(value)):
                    completed = self._preflight(stage, value)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, '')
                    self.assertEqual(completed.stderr, '')
                    self.assertEqual(completed.ssh_log, '')

    def test_mismatched_stage_and_confirmation_fails_before_ssh(self):
        for stage in STAGES:
            for confirmation in STAGES:
                if confirmation == stage:
                    continue
                with self.subTest(stage=stage, confirmation=confirmation):
                    completed = self._preflight(stage, confirmation)
                    self.assertEqual(completed.returncode, 1)
                    self.assertIn('type the selected stage to continue.', completed.stderr)
                    self.assertNotIn(confirmation, completed.stdout)
                    self.assertNotIn(confirmation, completed.stderr)
                    self.assertEqual(completed.ssh_log, '')

    def test_malformed_confirmation_fails_before_ssh(self):
        rejected = [
            '',
            '    ',
            'verify',
            'Verify',
            'VER IFY',
            'VERIFY BUILD_DRAFT',
            'please VERIFY',
            'VERIFY please',
            'VERIFY\n',
            'VERIFY\r',
            'VERIFY\r\n',
            '\nVERIFY',
            '\rVERIFY',
            '\tVERIFY',
            'VERIFY\t',
            ' \tVERIFY',
            'VERIFY\t ',
            'VERIFY\x7f',
            '\x01VERIFY',
            'VERIFY\x1b',
            'NOT-A-STAGE',
        ]
        refusal = 'Refusing to build: type the selected stage to continue.'
        for value in rejected:
            with self.subTest(value=repr(value)):
                completed = self._preflight('VERIFY', value)
                self.assertEqual(completed.returncode, 1)
                self.assertIn('type the selected stage to continue.', completed.stderr)
                if value and value not in refusal:
                    self.assertNotIn(value, completed.stdout)
                    self.assertNotIn(value, completed.stderr)
                self.assertEqual(completed.ssh_log, '')
                self.assertNotIn('ssh-keyscan', completed.stdout)

    def test_illegal_stage_fails_before_ssh_without_echoing_it(self):
        for stage in ('', 'verify', 'BUILD', 'VERIFY ', 'START-1970', 'VERIFY\n', 'BUILD_DRAFT\t'):
            with self.subTest(stage=repr(stage)):
                completed = self._preflight(stage, stage)
                self.assertEqual(completed.returncode, 1)
                self.assertIn('stage is not legal', completed.stderr)
                if stage and stage not in 'Refusing to build: the stage is not legal.':
                    self.assertNotIn(stage, completed.stdout)
                    self.assertNotIn(stage, completed.stderr)
                self.assertEqual(completed.ssh_log, '')

    def test_missing_values_and_wrong_refs_fail_before_ssh(self):
        missing_confirmation = self._preflight('VERIFY', include_confirmation=False)
        self.assertEqual(missing_confirmation.returncode, 1)
        self.assertEqual(missing_confirmation.ssh_log, '')
        missing_stage = self._preflight(include_stage=False)
        self.assertEqual(missing_stage.returncode, 1)
        self.assertIn('stage is not legal', missing_stage.stderr)
        self.assertEqual(missing_stage.ssh_log, '')
        missing_ref = self._preflight(include_ref=False)
        self.assertEqual(missing_ref.returncode, 1)
        self.assertIn('Only main is built.', missing_ref.stderr)
        self.assertEqual(missing_ref.ssh_log, '')
        for ref in (
            '',
            'main',
            'refs/heads/master',
            'refs/heads/Main',
            'refs/heads/main ',
            'refs/heads/feature',
            'refs/pull/1/merge',
            'refs/tags/main',
        ):
            with self.subTest(ref=repr(ref)):
                completed = self._preflight('PUBLISH', ref=ref)
                self.assertEqual(completed.returncode, 1)
                self.assertIn('Only main is built.', completed.stderr)
                self.assertIn(ref, completed.stderr)
                self.assertNotIn('PUBLISH', completed.stderr)
                self.assertEqual(completed.ssh_log, '')

    def test_preflight_source_does_not_connect_or_print_the_confirmation(self):
        source = PREFLIGHT.read_text()
        syntax = subprocess.run(['bash', '-n', str(PREFLIGHT)], check=False)
        self.assertEqual(syntax.returncode, 0)
        for forbidden in (
            'ssh-keyscan',
            'ssh ',
            'curl',
            'pythonanywhere.com',
            'manage.py',
            'mysql',
            'API_TOKEN',
            'PRIVATE_KEY',
            'BEGIN OPENSSH',
            'eval ',
            'source ',
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn('eval', _executable_lines(PREFLIGHT))
        self.assertIn('${confirmation# }', source)
        self.assertIn('${confirmation% }', source)
        for line in source.splitlines():
            if line.strip().startswith('echo '):
                self.assertNotIn('CONFIRMATION', line)
                self.assertNotIn('confirmation', line)
                self.assertNotIn('${stage}', line)
