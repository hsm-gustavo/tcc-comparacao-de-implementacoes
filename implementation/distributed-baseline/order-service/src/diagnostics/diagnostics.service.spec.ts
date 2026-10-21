import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
import { DataSource } from 'typeorm';
import {
  diagnosticContext,
  diagnosticHttpConfig,
  withDiagnosticOperation,
} from './context';
import { DiagnosticsService } from './diagnostics.service';
import { ObservedPool } from './pool-observer';

interface TestPool extends ObservedPool {
  end(): Promise<void>;
}
const { Pool } = createRequire(__filename)('pg') as {
  Pool: new (options: Record<string, unknown>) => TestPool;
};
type Summary = { count: number; failures: number; avgMs: number | null };
type Sample = {
  runId: string;
  pool: { total: number; idle: number; inUse: number; waiting: number };
  connectionAcquire: Summary;
  connectionHold: Summary;
  pool_hold_ms: number | null;
  poolHoldByStage: Array<{
    stage: string;
    requests: number;
    acquired: number;
    released: number;
    acquireFailures: number;
    holdMsTotal: number;
  }>;
  queryDuration: Summary;
  queryDurations: Record<string, Summary>;
  holders: Record<string, unknown>[];
  errors: Record<string, unknown>[];
  processMemory: { rss: number };
  processCpu: { userMs: number };
  skippedWindows: number;
};

// Real installed pg-pool and TypeORM query runner, but zero network/database I/O.
class MemoryClient extends EventEmitter {
  processID = 1234;
  _queryable = true;
  _ending = false;
  connect(callback: () => void) {
    process.nextTick(callback);
  }
  end(callback?: () => void) {
    this.emit('end');
    callback?.();
  }
  async query(sql: string) {
    await new Promise((resolve) => setTimeout(resolve, 5));
    if (sql === 'FAIL')
      throw Object.assign(new Error('secret-value'), { code: 'TEST' });
    return { rows: [], rowCount: 1, command: 'UPDATE' };
  }
}

describe('diagnostics with real pool/TypeORM and in-memory clients', () => {
  let pool: TestPool;
  let source: DataSource;
  let service: DiagnosticsService;
  let records: Sample[];
  let output: jest.SpyInstance;
  const originalFlag = process.env.DB_DIAGNOSTICS;
  const originalRun = process.env.RUN_ID;
  const checkout = () =>
    source.driver.obtainMasterConnection() as Promise<
      [MemoryClient, () => void]
    >;

  beforeEach(() => {
    process.env.DB_DIAGNOSTICS = '1';
    process.env.RUN_ID = 'unit-run';
    records = [];
    output = jest.spyOn(process.stdout, 'write').mockImplementation((chunk) => {
      if (String(chunk).startsWith('{'))
        records.push(JSON.parse(String(chunk)) as Sample);
      return true;
    });
    // max=1 and 15ms apply ONLY to this disconnected in-memory test pool.
    pool = new Pool({
      Client: MemoryClient,
      max: 1,
      connectionTimeoutMillis: 15,
    });
    source = new DataSource({ type: 'postgres' });
    (source.driver as unknown as { master: TestPool }).master = pool;
    service = new DiagnosticsService(source);
  });

  afterEach(async () => {
    service.onModuleDestroy();
    await pool.end();
    output.mockRestore();
    if (originalFlag === undefined) delete process.env.DB_DIAGNOSTICS;
    else process.env.DB_DIAGNOSTICS = originalFlag;
    if (originalRun === undefined) delete process.env.RUN_ID;
    else process.env.RUN_ID = originalRun;
  });

  it('measures actual checkout timeouts, preserves context, and shows live holders', async () => {
    service.onModuleInit();
    const client = await diagnosticContext.run({ requestId: 'holder' }, () =>
      checkout(),
    );
    const failure = diagnosticContext.run({ requestId: 'waiter' }, () =>
      withDiagnosticOperation('database.order.save', {}, () => checkout()),
    );
    await expect(failure).rejects.toThrow(
      'timeout exceeded when trying to connect',
    );
    service.sample();
    const sample = records.at(-1)!;
    expect(sample.runId).toBe('unit-run');
    expect(sample.pool).toEqual({ total: 1, idle: 0, inUse: 1, waiting: 0 });
    expect(sample.connectionAcquire).toMatchObject({ count: 2, failures: 1 });
    expect(sample.holders[0]).toMatchObject({
      backendPid: 1234,
      requestId: 'holder',
    });
    expect(sample.errors[0]).toMatchObject({
      requestId: 'waiter',
      operation: 'database.order.save',
    });
    client[1]();
    service.sample();
    expect(records.at(-1)!.connectionHold.count).toBe(1);
    expect(records.at(-1)!.pool_hold_ms).toBe(
      records.at(-1)!.connectionHold.avgMs,
    );
    expect(records.at(-1)!.holders).toEqual([]);
  });

  it('observes SQL after checkout, includes failed queries without values, and resets windows', async () => {
    source.logger.logQueryError = jest.fn();
    service.onModuleInit();
    const runner = source.createQueryRunner();
    await withDiagnosticOperation(
      'database.order.confirm',
      { orderId: 'order-1' },
      () => runner.query('UPDATE "orders" SET status = $1', ['secret-value']),
    );
    await expect(runner.query('FAIL', ['secret-value'])).rejects.toThrow(
      'secret-value',
    );
    // Startup is the only output so far: there is no per-query/acquisition log.
    expect(records).toHaveLength(1);
    service.sample();
    const sample = records.at(-1)!;
    expect(sample.queryDurations['UPDATE order']).toMatchObject({
      count: 1,
      failures: 0,
    });
    expect(sample.queryDurations.OTHER).toMatchObject({
      count: 1,
      failures: 1,
    });
    expect(sample.queryDurations['UPDATE order'].avgMs).toBeGreaterThan(0);
    expect(JSON.stringify(sample)).not.toContain('secret-value');
    expect(sample.processMemory.rss).toBeGreaterThan(0);
    expect(sample.processCpu.userMs).toBeGreaterThanOrEqual(0);
    await runner.release();
    service.sample();
    expect(records.at(-1)!.queryDurations['UPDATE order'].count).toBe(0);
    expect(records.at(-1)!.connectionHold.count).toBe(1);
  });

  it('delegates entirely when disabled and propagates headers only when enabled', () => {
    const connect = pool.connect;
    process.env.DB_DIAGNOSTICS = '0';
    service.onModuleInit();
    expect(pool.connect).toBe(connect);
    expect(source.subscribers).toHaveLength(0);
    expect(diagnosticHttpConfig()).toBeUndefined();
    expect(records).toHaveLength(0);
    process.env.DB_DIAGNOSTICS = '1';
    diagnosticContext.run({ requestId: 'request-id' }, () => {
      expect(diagnosticHttpConfig()).toEqual({
        headers: { 'X-Request-Id': 'request-id', 'X-Run-Id': 'unit-run' },
      });
    });
  });

  it('counts overlapping queries on one runner without serializing them', async () => {
    service.onModuleInit();
    const runner = source.createQueryRunner();
    await runner.connect();
    await Promise.all([runner.query('SELECT 1'), runner.query('SELECT 2')]);
    service.sample();
    expect(records.at(-1)!.queryDuration.count).toBe(2);
    expect(records.at(-1)!.queryDurations.SELECT.count).toBe(2);
    await runner.release();
  });

  it('drops a backpressured window rather than buffering unbounded logs', () => {
    service.onModuleInit();
    const state = (
      process.stdout as unknown as { _writableState: { needDrain: boolean } }
    )._writableState;
    const previous = state.needDrain;
    try {
      state.needDrain = true;
      service.sample();
      expect(records).toHaveLength(1);
    } finally {
      state.needDrain = previous;
    }
    service.sample();
    expect(records.at(-1)!.skippedWindows).toBe(1);
  });

  it('counts multiple checkouts per pilot request, retains skipped deltas, and excludes untagged work', async () => {
    service.onModuleInit();
    expect(service.recordPilotRequest('foreign-rate100_run1')).toBeUndefined();
    const stage = service.recordPilotRequest('unit-run-rate100_run1');
    expect(stage).toBe('unit-run-rate100_run1');
    await diagnosticContext.run({ pilotStage: stage }, async () => {
      const first = await checkout();
      const failure = checkout();
      await expect(failure).rejects.toThrow('timeout exceeded');
      first[1]();
      const second = await checkout();
      second[1]();
    });
    const untagged = await checkout();
    untagged[1]();
    const state = (
      process.stdout as unknown as { _writableState: { needDrain: boolean } }
    )._writableState;
    const previous = state.needDrain;
    try {
      state.needDrain = true;
      service.sample();
      expect(records).toHaveLength(1);
    } finally {
      state.needDrain = previous;
    }
    service.sample();
    const sample = records.at(-1)!;
    // The general window histogram was skipped/reset, but cohort counters survive.
    expect(sample.pool_hold_ms).toBeNull();
    expect(sample.poolHoldByStage).toHaveLength(1);
    expect(sample.poolHoldByStage[0]).toMatchObject({
      stage,
      requests: 1,
      acquired: 2,
      released: 2,
      acquireFailures: 1,
    });
    expect(sample.poolHoldByStage[0].holdMsTotal).toBeGreaterThan(0);
    service.sample();
    expect(records.at(-1)!.poolHoldByStage).toEqual([]);
  });
});
