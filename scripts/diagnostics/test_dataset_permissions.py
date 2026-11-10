"""Exercise real filesystem permissions without sudo, Docker or any benchmark."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


HELPER = Path(__file__).with_name('dataset-permissions.sh')


class DatasetPermissionsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.out = self.root / 'experiments/datasets/pilot/test-run'

    def run_helper(self, commands, sudo=True):
        # Exercise the sudo branch with our actual UID/GID: real chown/chmod are
        # permitted for our own fixtures, so these tests need no root privileges.
        owner = f'{os.getuid()}:{os.getgid()}' if sudo else ''
        script = f'''set -euo pipefail
source {shlex.quote(str(HELPER))}
dataset_sudo_owner() {{ printf '%s' {shlex.quote(owner)}; }}
umask 077
{commands}
'''
        return subprocess.run(['bash', '-c', script, 'test', str(self.root), str(self.out)],
                              capture_output=True, text=True, timeout=10)

    def test_repairs_private_ancestors_without_recursing_into_previous_runs(self):
        self.out.parent.mkdir(parents=True)
        old_run = self.out.parent / 'old-run'
        old_run.mkdir(mode=0o700)
        old_file = old_run / 'keep.log'
        old_file.write_text('previous evidence')
        old_file.chmod(0o400)
        datasets = self.root / 'experiments/datasets'
        datasets.chmod(0o000)
        # Restore on assertion failures as well, so TemporaryDirectory can clean.
        self.addCleanup(datasets.chmod, 0o700)
        result = self.run_helper('dataset_prepare_parent "$1" "$2"\nmkdir "$2"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(datasets.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o700)
        self.assertEqual(old_file.stat().st_mode & 0o777, 0o400)
        self.assertEqual(old_file.read_text(), 'previous evidence')

    def test_returns_nested_analysis_and_status_without_opening_access_to_others(self):
        result = self.run_helper('''dataset_prepare_parent "$1" "$2"
mkdir "$2" "$2/analysis"
printf 'report' >"$2/analysis/summary.json"
: >"$2/status.json"
chmod 000 "$2/analysis/summary.json"
dataset_return_ownership "$2"
printf 'completed' >"$2/status.json"
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (self.out, self.out / 'analysis', self.out / 'analysis/summary.json',
                     self.out / 'status.json'):
            self.assertEqual(path.stat().st_uid, os.getuid())
            self.assertEqual(path.stat().st_gid, os.getgid())
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.assertEqual((self.out / 'analysis/summary.json').read_text(), 'report')
        self.assertEqual((self.out / 'status.json').read_text(), 'completed')

    def test_does_not_follow_links_to_files_outside_the_dataset(self):
        external = self.root / 'external'
        external.mkdir()
        report = external / 'keep.log'
        report.write_text('untouched')
        report.chmod(0o400)
        self.out.mkdir(parents=True)
        (self.out / 'external-file').symlink_to(report)
        (self.out / 'external-directory').symlink_to(external, target_is_directory=True)
        result = self.run_helper('dataset_return_ownership "$2"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report.stat().st_mode & 0o777, 0o400)
        self.assertEqual(report.read_text(), 'untouched')

    def test_refuses_symlinked_dataset_ancestor(self):
        (self.root / 'experiments').mkdir()
        external = self.root / 'external'
        external.mkdir()
        (self.root / 'experiments/datasets').symlink_to(external, target_is_directory=True)
        result = self.run_helper('dataset_prepare_parent "$1" "$2"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing a symlink', result.stderr)
        self.assertEqual(list(external.iterdir()), [])

    def test_non_sudo_run_keeps_private_permissions(self):
        result = self.run_helper('''dataset_prepare_parent "$1" "$2"
mkdir "$2"
printf 'private' >"$2/application.log"
dataset_return_ownership "$2"
''', sudo=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.out / 'application.log').stat().st_mode & 0o777, 0o600)

    def test_repairs_every_parent_of_a_nested_suite(self):
        self.out = self.root / 'experiments/datasets/pilot/suite/tcc'
        self.out.parent.mkdir(parents=True)
        pilot = self.root / 'experiments/datasets/pilot'
        pilot.chmod(0o000)
        self.addCleanup(pilot.chmod, 0o700)
        result = self.run_helper('dataset_prepare_parent "$1" "$2"\nmkdir "$2"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pilot.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.out.parent.stat().st_uid, os.getuid())


if __name__ == '__main__':
    unittest.main()
