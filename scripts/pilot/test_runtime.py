"""Build and initialize the shared Nest observer with an in-memory DataSource."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SERVICE=ROOT/'implementation/distributed-baseline/order-service'


class RuntimeTest(unittest.TestCase):
    def test_observer_injection_uses_the_existing_pool_and_detaches_on_close(self):
        compiler=SERVICE/'node_modules/.bin/tsc'
        if not compiler.exists() or not shutil.which('node'):
            self.skipTest('Install the baseline order-service dependencies to test the Nest integration')
        with tempfile.TemporaryDirectory() as directory:
            app=Path(directory)
            (app/'pilot').mkdir()
            shutil.copyfile(ROOT/'environment/pilot/register-diagnostics.cjs',app/'pilot/register-diagnostics.cjs')
            (app/'node_modules').symlink_to(SERVICE/'node_modules',target_is_directory=True)
            build=subprocess.run([str(compiler),'--project','tsconfig.build.json','--incremental','false',
                                  '--outDir',str(app/'dist')],cwd=SERVICE,capture_output=True,text=True,timeout=40)
            self.assertEqual(build.returncode,0,build.stdout+build.stderr)
            (app/'check.cjs').write_text('''require('reflect-metadata');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { Global, Module } = require('@nestjs/common');
const { NestFactory } = require('@nestjs/core');
const { DataSource } = require('typeorm');
const { DiagnosticsService } = require('./dist/diagnostics/diagnostics.service');
const { diagnosticContext } = require('./dist/diagnostics/context');
const pool = new EventEmitter();
pool.options = { max: 80, connectionTimeoutMillis: 2000, idleTimeoutMillis: 30000 };
pool.totalCount = 1; pool.idleCount = 1; pool.waitingCount = 0;
pool.connect = function(callback) {
  const client = { processID: 123 };
  this.emit('acquire', client);
  callback(null, client, () => this.emit('release', undefined, client));
};
const original = pool.connect;
const source = { driver: { master: pool }, subscribers: [] };
class DatabaseModule {}
Global()(DatabaseModule);
Module({ providers: [{ provide: DataSource, useValue: source }], exports: [DataSource] })(DatabaseModule);
class ApplicationModule {}
Module({ imports: [DatabaseModule] })(ApplicationModule);
(async () => {
  const app = await NestFactory.create(ApplicationModule, { logger: false });
  await app.init();
  assert.equal(source.subscribers.length, 1);
  assert.notEqual(pool.connect, original);
  diagnosticContext.run({ requestId: 'fixture', pilotStage: 'runtime-rate1_run1' }, () => {
    pool.connect((error, client, release) => {
      assert.equal(error, null);
      const queryRunner = { databaseConnection: client };
      source.subscribers[0].beforeQuery({queryRunner, query: 'COMMIT'});
      source.subscribers[0].afterQuery({queryRunner, query: 'COMMIT', success: true});
      release();
    });
  });
  app.get(DiagnosticsService).sample();
  await app.close();
  assert.equal(source.subscribers.length, 0);
  assert.equal(pool.connect, original);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')
            env=dict(os.environ,PILOT_INJECT_DIAGNOSTICS='1',DB_DIAGNOSTICS='1',RUN_ID='runtime')
            result=subprocess.run(['node','--require','./pilot/register-diagnostics.cjs','check.cjs'],cwd=app,
                                  env=env,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('"type":"diagnostics_started"',result.stdout)
            self.assertIn('"COMMIT":{"count":1',result.stdout)
            self.assertIn('"max":80',result.stdout)


if __name__=='__main__':unittest.main()
