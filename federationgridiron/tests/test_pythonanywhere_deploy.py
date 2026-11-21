"""The PythonAnywhere update stops unless main can fast-forward.

These tests use temporary local repositories. They do not open SSH, call the
PythonAnywhere API, or read credentials.
"""

import os
import subprocess
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]
UPDATE = ROOT / 'scripts' / 'pythonanywhere_ff_update.sh'
KNOWN_HOST = ROOT / 'scripts' / 'pythonanywhere_known_host.sh'
WORKFLOW = ROOT / '.github' / 'workflows' / 'deploy-pythonanywhere.yml'
PUBLISHED_FINGERPRINT = 'SHA256:zy2jmqxNg/fs6tFZK55OjHTI3B2UofzOiUvTPtcX3/Y'


def _git_env():
    env = os.environ.copy()
    env.update({
        'GIT_AUTHOR_NAME': 'Deploy Test',
        'GIT_AUTHOR_EMAIL': 'deploy-test@example.com',
        'GIT_COMMITTER_NAME': 'Deploy Test',
        'GIT_COMMITTER_EMAIL': 'deploy-test@example.com',
        'GIT_TERMINAL_PROMPT': '0',
    })
    return env


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


class PythonAnywhereUpdateTests(SimpleTestCase):
    def setUp(self):
        root = Path(self._tmpdir())
        self.origin = root / 'origin'
        self.checkout = root / 'checkout'
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

    def _tmpdir(self):
        base = Path(os.environ.get('TMPDIR', '/tmp'))
        path = base / f'fg-deploy-{os.getpid()}-{id(self)}'
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(subprocess.run, ['rm', '-rf', str(path)], check=False)
        return path

    def _run(self, checkout=None):
        return subprocess.run(
            ['bash', str(UPDATE), str(checkout or self.checkout)],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )

    def test_fast_forward_updates_only_main(self):
        before = _head(self.checkout)
        _commit(self.origin, 'next.txt', 'next\n')
        completed = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(_head(self.checkout), _head(self.origin))
        self.assertNotEqual(_head(self.checkout), before)
        self.assertIn('Updated main to', completed.stdout)

    def test_current_main_stays_in_place(self):
        before = _head(self.checkout)
        completed = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(_head(self.checkout), before)

    def test_dirty_checkout_is_left_unchanged(self):
        before = _head(self.checkout)
        _commit(self.origin, 'next.txt', 'next\n')
        readme = self.checkout / 'README'
        readme.write_text('local edit\n')
        completed = self._run()
        self.assertEqual(completed.returncode, 1)
        self.assertIn('dirty', completed.stderr)
        self.assertEqual(_head(self.checkout), before)
        self.assertFalse((self.checkout / 'next.txt').exists())

    def test_untracked_file_is_refused(self):
        before = _head(self.checkout)
        (self.checkout / 'local-note.txt').write_text('leave me\n')
        completed = self._run()
        self.assertEqual(completed.returncode, 1)
        self.assertIn('dirty', completed.stderr)
        self.assertEqual(_head(self.checkout), before)

    def test_non_fast_forward_is_left_unchanged(self):
        _commit(self.checkout, 'local.txt', 'local\n')
        before = _head(self.checkout)
        _commit(self.origin, 'remote.txt', 'remote\n')
        completed = self._run()
        self.assertNotEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(_head(self.checkout), before)
        self.assertFalse((self.checkout / 'remote.txt').exists())

    def test_other_branch_is_refused(self):
        _git(self.checkout, 'checkout', '-b', 'feature')
        before = _head(self.checkout)
        completed = self._run()
        self.assertEqual(completed.returncode, 1)
        self.assertIn('not main', completed.stderr)
        self.assertEqual(_head(self.checkout), before)
        self.assertEqual(
            _git(self.checkout, 'rev-parse', '--abbrev-ref', 'HEAD').stdout.strip(),
            'feature',
        )

    def test_missing_checkout_is_refused(self):
        completed = self._run(self.checkout / 'missing')
        self.assertEqual(completed.returncode, 1)
        self.assertIn('not a git checkout', completed.stderr)

    def test_update_script_does_not_migrate_or_reload(self):
        commands = '\n'.join(
            line for line in UPDATE.read_text().splitlines()
            if line.strip() and not line.strip().startswith('#')
        )
        for forbidden in ('migrate', 'mysql', 'reload', 'manage.py', 'FG_SECRET', 'API_TOKEN'):
            self.assertNotIn(forbidden, commands)


class PythonAnywhereKnownHostTests(SimpleTestCase):
    def test_matching_fingerprint_is_selected(self):
        key_dir = Path(self._tmpdir())
        private = key_dir / 'id'
        subprocess.run(
            ['ssh-keygen', '-t', 'ed25519', '-f', str(private), '-N', '', '-q'],
            check=True,
            capture_output=True,
            text=True,
        )
        public = (private.with_suffix('.pub')).read_text().strip()
        fingerprint = subprocess.run(
            ['ssh-keygen', '-lf', str(private.with_suffix('.pub')), '-E', 'sha256'],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()[1]
        decoy = 'ssh.pythonanywhere.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyNotUsedForMatching\n'
        scanned = f'# comment\n{decoy}ssh.pythonanywhere.com {public}\n'
        completed = subprocess.run(
            ['bash', str(KNOWN_HOST), fingerprint],
            input=scanned,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), f'ssh.pythonanywhere.com {public}')

    def test_published_fingerprint_constant_is_in_the_workflow(self):
        self.assertIn(PUBLISHED_FINGERPRINT, WORKFLOW.read_text())

    def test_other_fingerprint_is_refused(self):
        completed = subprocess.run(
            ['bash', str(KNOWN_HOST), PUBLISHED_FINGERPRINT],
            input='ssh.pythonanywhere.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyNotUsedForMatching\n',
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn('does not match', completed.stderr)
        self.assertEqual(completed.stdout, '')

    def _tmpdir(self):
        path = Path(os.environ.get('TMPDIR', '/tmp')) / f'fg-host-{os.getpid()}-{id(self)}'
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(subprocess.run, ['rm', '-rf', str(path)], check=False)
        return path


class PythonAnywhereWorkflowTests(SimpleTestCase):
    def test_workflow_deploys_main_only_and_reloads_after_the_update(self):
        source = WORKFLOW.read_text()
        self.assertIn('branches:\n      - main', source)
        self.assertIn('workflow_dispatch:', source)
        self.assertNotIn('pull_request', source)
        self.assertIn('cancel-in-progress: false', source)
        self.assertIn("github.ref == 'refs/heads/main'", source)
        self.assertIn('secrets.PYTHONANYWHERE_SSH_PRIVATE_KEY', source)
        self.assertIn('secrets.PYTHONANYWHERE_API_TOKEN', source)
        self.assertIn('/home/lecrolink/federationgridiron', source)
        self.assertIn(
            'https://www.pythonanywhere.com/api/v0/user/lecrolink/webapps/www.federationgridiron.com/reload/',
            source,
        )
        self.assertLess(
            source.index('Fast-forward the PythonAnywhere checkout'),
            source.index('Reload www.federationgridiron.com'),
        )
        for forbidden in ('manage.py', 'migrate', 'BEGIN OPENSSH', 'BEGIN RSA'):
            self.assertNotIn(forbidden, source)

    def test_dashboard_stylesheet_is_already_in_the_served_static_directory(self):
        """The web app serves checkout static files. The update does not collect them."""
        source = ROOT / 'play' / 'static' / 'play' / 'play.css'
        served = ROOT / 'static' / 'play' / 'play.css'
        template = (ROOT / 'play' / 'templates' / 'play' / 'base.html').read_text()
        self.assertIn("{% static 'play/play.css' %}", template)
        self.assertEqual(served.read_bytes(), source.read_bytes())
        self.assertIn(b'font-family', served.read_bytes())
