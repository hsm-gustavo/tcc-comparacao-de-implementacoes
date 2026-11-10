"""Exercise real pnpm lifecycle approvals with local fixtures; no network or Docker."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / 'environment/pilot/pnpm-workspace.yaml'
PNPM = shutil.which('pnpm')
APPROVED = ('@nestjs/core', '@scarf/scarf', 'unrs-resolver')


class BuildPolicyTest(unittest.TestCase):
    def test_policy_matches_baseline_and_precedes_install(self):
        baseline = ROOT / 'implementation/distributed-baseline/order-service/pnpm-workspace.yaml'
        self.assertEqual(POLICY.read_bytes(), baseline.read_bytes())
        dockerfile = (ROOT / 'environment/pilot/Dockerfile').read_text()
        self.assertLess(dockerfile.index('COPY implementation/${ARCHITECTURE}'),
                        dockerfile.index('COPY environment/pilot/pnpm-workspace.yaml'))
        self.assertLess(dockerfile.index('COPY environment/pilot/pnpm-workspace.yaml'),
                        dockerfile.index('pnpm install --frozen-lockfile'))

    def install_fixture(self, *, policy, extra_package=False):
        if not PNPM:
            self.skipTest('pnpm is required for the offline lifecycle integration test')
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        dependencies = {}
        names = (*APPROVED, 'unapproved-fixture') if extra_package else APPROVED
        for index, name in enumerate(names):
            archive = root / f'dependency-{index}.tgz'
            files = {
                'package.json': json.dumps({'name': name, 'version': '1.0.0',
                                           'scripts': {'postinstall': 'node marker.cjs'}}),
                'marker.cjs': "require('node:fs').writeFileSync('build-ran', 'yes');\n",
            }
            with tarfile.open(archive, 'w:gz') as output:
                for filename, content in files.items():
                    encoded = content.encode()
                    info = tarfile.TarInfo(f'package/{filename}')
                    info.size = len(encoded)
                    output.addfile(info, io.BytesIO(encoded))
            dependencies[name] = 'file:' + str(archive)
        (root / 'package.json').write_text(json.dumps({
            'name': 'offline-build-policy-fixture', 'version': '1.0.0', 'private': True,
            'dependencies': dependencies,
        }))
        settings = POLICY.read_text() if policy else ''
        (root / 'pnpm-workspace.yaml').write_text(settings + '\nstrictDepBuilds: true\n')
        command = [PNPM, 'install', '--offline', '--store-dir', str(root / 'store')]
        env = dict(os.environ, CI='true', NO_UPDATE_NOTIFIER='1')
        if policy:
            configured = subprocess.run([PNPM, 'config', 'get', 'allowBuilds', '--json'],
                                        cwd=root, env=env, capture_output=True,
                                        text=True, timeout=30)
            self.assertEqual(configured.returncode, 0, configured.stdout + configured.stderr)
            approved = json.loads(configured.stdout)
            self.assertEqual(approved, {name: True for name in APPROVED})
            # Real services use registry packages. Offline fixture tarballs require
            # exact artifact approval: a bare registry name cannot approve file: URLs.
            # Derive these fixture-only rules from the policy actually loaded by pnpm.
            for index, name in enumerate(names):
                if approved.get(name) is True:
                    key = json.dumps(f'{name}@file:dependency-{index}.tgz')
                    settings += f'  {key}: true\n'
            (root / 'pnpm-workspace.yaml').write_text(settings + '\nstrictDepBuilds: true\n')
        # Only fixture lockfile creation skips scripts. The install under test does not.
        prepared = subprocess.run(command + ['--lockfile-only', '--ignore-scripts'],
                                  cwd=root, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        lockfile = root / 'pnpm-lock.yaml'
        before = lockfile.read_bytes()
        installed = subprocess.run(command + ['--frozen-lockfile'], cwd=root, env=env,
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(lockfile.read_bytes(), before, 'Frozen install modified the lockfile')
        return root, installed

    def test_real_install_runs_approved_scripts_without_changing_lockfile(self):
        root, result = self.install_fixture(policy=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in APPROVED:
            self.assertEqual((root / 'node_modules' / name / 'build-ran').read_text(), 'yes')

    def test_real_install_reproduces_failure_without_policy(self):
        _, result = self.install_fixture(policy=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ERR_PNPM_IGNORED_BUILDS', result.stdout + result.stderr)

    def test_real_install_still_rejects_unapproved_scripts(self):
        _, result = self.install_fixture(policy=True, extra_package=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ERR_PNPM_IGNORED_BUILDS', result.stdout + result.stderr)
        self.assertIn('unapproved-fixture', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
