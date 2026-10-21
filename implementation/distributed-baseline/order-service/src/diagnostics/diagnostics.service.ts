import { Injectable, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import {
  createHistogram,
  monitorEventLoopDelay,
  performance,
} from 'node:perf_hooks';
import {
  AfterQueryEvent,
  BeforeQueryEvent,
  DataSource,
  EntitySubscriberInterface,
  QueryRunner,
} from 'typeorm';
import {
  diagnosticContext,
  DiagnosticContext,
  diagnosticsEnabled,
} from './context';
import { observePool, ObservedPool, PoolClient } from './pool-observer';

class Durations {
  private readonly histogram = createHistogram();
  private failures = 0;

  record(ms: number, success = true) {
    this.histogram.record(Math.max(1, Math.round(ms * 1e6)));
    if (!success) this.failures++;
  }

  snapshot() {
    const h = this.histogram;
    const ms = (value: number) => Number((value / 1e6).toFixed(3));
    const result = {
      count: h.count,
      failures: this.failures,
      avgMs: h.count ? ms(h.mean) : null,
      p50Ms: h.count ? ms(h.percentile(50)) : null,
      p95Ms: h.count ? ms(h.percentile(95)) : null,
      p99Ms: h.count ? ms(h.percentile(99)) : null,
      maxMs: h.count ? ms(h.max) : null,
    };
    h.reset();
    this.failures = 0;
    return result;
  }
}

type Hold = {
  id: number;
  started: number;
  backendPid?: number;
  context: DiagnosticContext;
  query?: { started: number; category: string };
};

type PilotPoolStats = {
  stage: string;
  requests: number;
  acquired: number;
  acquireFailures: number;
  released: number;
  removedWithoutRelease: number;
  holdMsTotal: number;
  lastReleaseEpochMs: number | null;
};

function queryCategory(sql: string): string {
  if (sql.startsWith('INSERT INTO "orders"')) return 'INSERT order';
  if (sql.startsWith('INSERT INTO "order_items"')) return 'INSERT order_item';
  if (sql.startsWith('UPDATE "orders"')) return 'UPDATE order';
  for (const verb of ['SELECT', 'START TRANSACTION', 'COMMIT', 'ROLLBACK']) {
    if (sql.startsWith(verb)) return verb;
  }
  return 'OTHER';
}

@Injectable()
export class DiagnosticsService implements OnModuleInit, OnModuleDestroy {
  private pool?: ObservedPool;
  private detach?: () => void;
  private timer?: NodeJS.Timeout;
  private readonly loop = monitorEventLoopDelay({ resolution: 20 });
  private readonly acquire = new Durations();
  private readonly hold = new Durations();
  private readonly queryDuration = new Durations();
  private readonly queries = new Map<string, Durations>();
  private readonly clients = new WeakMap<PoolClient, Hold>();
  // Only metadata, never client references: a forgotten client is not kept alive.
  private readonly active = new Map<number, Hold>();
  private readonly queryStarts = new WeakMap<
    QueryRunner,
    Array<{ started: number; category: string; sql: string }>
  >();
  private errors: Record<string, unknown>[] = [];
  private errorCount = 0;
  private sequence = 0;
  private previousTime = performance.now();
  private previousCpu = process.cpuUsage();
  private skippedWindows = 0;
  // Deltas by pilot cohort, retained across skipped stdout windows. No client,
  // request or SQL values are retained here; at most one entry per stage/window.
  private readonly pilotStages = new Map<string, PilotPoolStats>();

  constructor(private readonly dataSource: DataSource) {}

  recordPilotRequest(stage?: string): string | undefined {
    const prefix = `${process.env.RUN_ID}-`;
    if (
      !process.env.RUN_ID ||
      !stage?.startsWith(prefix) ||
      !/^(warmup|rate[1-9][0-9]*_run[1-9][0-9]*)$/.test(
        stage.slice(prefix.length),
      )
    )
      return undefined;
    this.pilotStats(stage).requests++;
    return stage;
  }

  private pilotStats(stage: string): PilotPoolStats {
    let stats = this.pilotStages.get(stage);
    if (!stats) {
      stats = {
        stage,
        requests: 0,
        acquired: 0,
        acquireFailures: 0,
        released: 0,
        removedWithoutRelease: 0,
        holdMsTotal: 0,
        lastReleaseEpochMs: null,
      };
      this.pilotStages.set(stage, stats);
    }
    return stats;
  }

  private readonly subscriber: EntitySubscriberInterface = {
    beforeQuery: (event: BeforeQueryEvent<unknown>) => {
      const started = performance.now();
      const pending = this.queryStarts.get(event.queryRunner) ?? [];
      pending.push({
        started,
        category: queryCategory(event.query),
        sql: event.query,
      });
      this.queryStarts.set(event.queryRunner, pending);
      const client = (
        event.queryRunner as QueryRunner & { databaseConnection?: PoolClient }
      ).databaseConnection;
      const hold = client && this.clients.get(client);
      if (hold) hold.query = pending[0];
    },
    afterQuery: (event: AfterQueryEvent<unknown>) => {
      const pending = this.queryStarts.get(event.queryRunner);
      const index =
        pending?.findIndex((query) => query.sql === event.query) ?? -1;
      if (!pending || index === -1) return;
      const [{ started }] = pending.splice(index, 1);
      if (!pending.length) this.queryStarts.delete(event.queryRunner);
      const category = queryCategory(event.query);
      let durations = this.queries.get(category);
      if (!durations) this.queries.set(category, (durations = new Durations()));
      const durationMs = performance.now() - started;
      durations.record(durationMs, event.success);
      this.queryDuration.record(durationMs, event.success);
      const client = (
        event.queryRunner as QueryRunner & { databaseConnection?: PoolClient }
      ).databaseConnection;
      const hold = client && this.clients.get(client);
      if (hold) hold.query = pending[0];
      if (!event.success) {
        // Driver messages may contain SQL values. Never copy them or parameters.
        this.errorCount++;
        if (this.errors.length < 5)
          this.errors.push({
            metric: 'query_duration',
            ...diagnosticContext.getStore(),
            category,
            backendPid: client?.processID,
            errorCode: (event.error as { code?: string } | undefined)?.code,
          });
      }
    },
  };

  onModuleInit() {
    if (!diagnosticsEnabled()) return;
    const pool = (
      this.dataSource.driver as unknown as { master?: ObservedPool }
    ).master;
    if (
      !pool ||
      typeof pool.connect !== 'function' ||
      typeof pool.waitingCount !== 'number'
    ) {
      throw new Error(
        'DB_DIAGNOSTICS: unsupported TypeORM master pool; check the installed driver',
      );
    }
    this.pool = pool;
    this.detach = observePool(pool, {
      acquire: (client) => {
        const hold: Hold = {
          id: ++this.sequence,
          started: performance.now(),
          backendPid: client.processID,
          context: {},
        };
        this.clients.set(client, hold);
        this.active.set(hold.id, hold);
      },
      delivered: (client, context) => {
        const hold = this.clients.get(client);
        if (hold) hold.context = context;
        if (context.pilotStage) this.pilotStats(context.pilotStage).acquired++;
      },
      release: (_error, client) => {
        const hold = this.clients.get(client);
        if (!hold) return;
        const now = performance.now();
        const durationMs = now - hold.started;
        this.hold.record(durationMs);
        if (hold.context.pilotStage) {
          const stats = this.pilotStats(hold.context.pilotStage);
          stats.released++;
          stats.holdMsTotal += durationMs;
          stats.lastReleaseEpochMs = performance.timeOrigin + now;
        }
        this.active.delete(hold.id);
        this.clients.delete(client);
      },
      remove: (client) => {
        const hold = this.clients.get(client);
        if (hold?.context.pilotStage)
          this.pilotStats(hold.context.pilotStage).removedWithoutRelease++;
        if (hold) this.active.delete(hold.id);
        this.clients.delete(client);
      },
      acquisition: (durationMs, error, context) => {
        this.acquire.record(durationMs, !error);
        if (error) {
          if (context.pilotStage)
            this.pilotStats(context.pilotStage).acquireFailures++;
          this.errorCount++;
          if (this.errors.length < 5)
            this.errors.push({
              metric: 'connection_acquire_duration',
              ...context,
              durationMs,
              error:
                error instanceof Error ? error.message : 'Unknown pool error',
            });
        }
      },
    });
    this.dataSource.subscribers.push(this.subscriber);
    this.previousTime = performance.now();
    this.previousCpu = process.cpuUsage();
    this.loop.enable();
    this.write({
      type: 'diagnostics_started',
      nodeVersion: process.version,
      pool: {
        max: pool.options.max,
        connectionTimeoutMillis: pool.options.connectionTimeoutMillis,
        idleTimeoutMillis: pool.options.idleTimeoutMillis,
      },
      eventLoopResolutionMs: 20,
      intervalMs: 1000,
    });
    this.timer = setInterval(() => this.sample(), 1000);
    this.timer.unref();
  }

  sample() {
    const pool = this.pool;
    if (!pool) return;
    const now = performance.now();
    const cpu = process.cpuUsage();
    const userMs = (cpu.user - this.previousCpu.user) / 1000;
    const systemMs = (cpu.system - this.previousCpu.system) / 1000;
    const windowMs = now - this.previousTime;
    const memory = process.memoryUsage();
    const holders = Array.from(this.active.values())
      .slice(0, 32)
      .map((hold) => ({
        backendPid: hold.backendPid,
        ...hold.context,
        holdMs: Number((now - hold.started).toFixed(3)),
        query: hold.query?.category,
        queryElapsedMs: hold.query
          ? Number((now - hold.query.started).toFixed(3))
          : undefined,
      }));
    const queryDurations = Object.fromEntries(
      Array.from(this.queries, ([category, durations]) => [
        category,
        durations.snapshot(),
      ]),
    );
    // One bounded JSON line per window. Never accumulate stdout writes if the
    // Docker logger/pipe cannot keep up; loss is visible in the next window.
    const connectionHold = this.hold.snapshot();
    const record = {
      type: 'db_diagnostic_sample',
      windowMs,
      pool: {
        total: pool.totalCount,
        idle: pool.idleCount,
        inUse: pool.totalCount - pool.idleCount,
        waiting: pool.waitingCount,
      },
      connectionAcquire: this.acquire.snapshot(),
      connectionHold,
      // Arithmetic mean of completed checkouts in this window, not HTTP latency
      // or time waiting for a connection. Null when no connection was returned.
      pool_hold_ms: connectionHold.avgMs,
      poolHoldByStage: Array.from(this.pilotStages.values()),
      queryDuration: this.queryDuration.snapshot(),
      queryDurations,
      holders,
      holdersTruncated: this.active.size > holders.length,
      errors: this.errors,
      errorCount: this.errorCount,
      eventLoop: {
        p50Ms: this.loop.count ? this.loop.percentile(50) / 1e6 : null,
        p95Ms: this.loop.count ? this.loop.percentile(95) / 1e6 : null,
        p99Ms: this.loop.count ? this.loop.percentile(99) / 1e6 : null,
        maxMs: this.loop.count ? this.loop.max / 1e6 : null,
        count: this.loop.count,
      },
      processCpu: {
        userMs,
        systemMs,
        percentOfOneCore: (100 * (userMs + systemMs)) / windowMs,
      },
      processMemory: {
        rss: memory.rss,
        heapUsed: memory.heapUsed,
        heapTotal: memory.heapTotal,
        external: memory.external,
      },
      skippedWindows: this.skippedWindows,
    };
    if (process.stdout.writableNeedDrain) this.skippedWindows++;
    else {
      this.write(record);
      this.pilotStages.clear();
      this.skippedWindows = 0;
    }
    this.errors = [];
    this.errorCount = 0;
    this.loop.reset();
    this.previousCpu = cpu;
    this.previousTime = now;
  }

  private write(record: Record<string, unknown>) {
    process.stdout.write(
      JSON.stringify({
        timestamp: new Date().toISOString(),
        service: 'order-service',
        runId: process.env.RUN_ID ?? null,
        pid: process.pid,
        ...record,
      }) + '\n',
    );
  }

  onModuleDestroy() {
    if (!this.pool) return;
    clearInterval(this.timer);
    this.sample();
    this.loop.disable();
    this.detach?.();
    const index = this.dataSource.subscribers.indexOf(this.subscriber);
    if (index !== -1) this.dataSource.subscribers.splice(index, 1);
    this.pool = undefined;
  }
}
