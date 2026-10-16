import { EventEmitter } from 'node:events';
import { performance } from 'node:perf_hooks';
import { diagnosticContext, DiagnosticContext } from './context';

export type PoolClient = object & { processID?: number };
// Structural view of TypeORM's internal master pool; no second pg dependency/pool.
export interface ObservedPool extends EventEmitter {
  totalCount: number;
  idleCount: number;
  waitingCount: number;
  options: {
    max?: number;
    connectionTimeoutMillis?: number;
    idleTimeoutMillis?: number;
  };
  connect: (...args: unknown[]) => unknown;
}

export interface PoolObserver {
  acquire(this: void, client: PoolClient): void;
  delivered(this: void, client: PoolClient, context: DiagnosticContext): void;
  release(this: void, error: unknown, client: PoolClient): void;
  remove(this: void, client: PoolClient): void;
  acquisition(
    this: void,
    durationMs: number,
    error: unknown,
    context: DiagnosticContext,
  ): void;
}

export function observePool(
  pool: ObservedPool,
  observer: PoolObserver,
): () => void {
  const original = pool.connect;
  // Deliberately not async. Preserve the return value and the callback's receiver,
  // arguments (including the original release), thrown errors and error objects.
  // TypeORM 0.3.28 uses this callback branch. Promise callers are delegated
  // untouched: attaching .then would change unhandled-rejection semantics.
  const wrapped = function (this: ObservedPool, ...args: unknown[]) {
    const callback = args[0];
    if (typeof callback !== 'function')
      return Reflect.apply(original, this, args);
    const started = performance.now();
    const context = { ...diagnosticContext.getStore() };
    let delivered = false;
    const observedCallback = function (this: unknown, ...result: unknown[]) {
      delivered = true;
      observer.acquisition(performance.now() - started, result[0], context);
      if (!result[0] && result[1]) {
        observer.delivered(result[1] as PoolClient, context);
      }
      return Reflect.apply(
        callback as (...args: unknown[]) => unknown,
        this,
        result,
      );
    };
    try {
      return Reflect.apply(original, this, [
        observedCallback,
        ...args.slice(1),
      ]);
    } catch (error) {
      if (!delivered)
        observer.acquisition(performance.now() - started, error, context);
      throw error;
    }
  };
  pool.connect = wrapped;
  pool.on('acquire', observer.acquire);
  pool.on('release', observer.release);
  pool.on('remove', observer.remove);
  return () => {
    if (pool.connect === wrapped) pool.connect = original;
    pool.off('acquire', observer.acquire);
    pool.off('release', observer.release);
    pool.off('remove', observer.remove);
  };
}
