"""Verify the same effective PostgreSQL pool contract in all active implementations."""
import json
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURES = ('distributed-baseline', 'tcc', 'two-phase-commit',
                 'orchestrated-saga', 'choreographed-saga')


class PoolConfigurationTest(unittest.TestCase):
    def test_same_defaults_and_environment_override_in_all_20_modules(self):
        files = []
        for architecture in ARCHITECTURES:
            files.extend(sorted((ROOT / 'implementation' / architecture).glob('*-service/src/app.module.ts')))
        self.assertEqual(len(files), 20)
        for file in files:
            with self.subTest(module=str(file.relative_to(ROOT))):
                extra = re.findall(r'extra:\s*(\{[^}]+\})', file.read_text())
                self.assertEqual(len(extra), 1)
                # Evaluate the actual options expression for default and overridden environments.
                js = ('const expression=JSON.parse(process.argv[1]);'
                      'const evaluate=new Function("process", "return ("+expression+")");'
                      'console.log(JSON.stringify([evaluate({env:{}}),'
                      'evaluate({env:{DB_POOL_MAX:"37"}})]));')
                output = subprocess.check_output(['node', '-e', js, json.dumps(extra[0])], text=True)
                default, overridden = json.loads(output)
                self.assertEqual(default, {'max': 80, 'connectionTimeoutMillis': 2000,
                                           'idleTimeoutMillis': 30000})
                self.assertEqual(overridden, {'max': 37, 'connectionTimeoutMillis': 2000,
                                              'idleTimeoutMillis': 30000})


if __name__ == '__main__':
    unittest.main()
