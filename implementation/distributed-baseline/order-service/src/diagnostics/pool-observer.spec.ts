import { EventEmitter } from 'node:events';
import { diagnosticContext } from './context';
import { observePool, ObservedPool, PoolObserver } from './pool-observer';

type Callback = (...args: unknown[]) => unknown;

describe('pool.connect observation semantics', () => {
  function fixture(connect: (...args: unknown[]) => unknown) {
    const pool = Object.assign(new EventEmitter(), {
      connect,
      totalCount: 0,
      idleCount: 0,
      waitingCount: 0,
      options: {},
    }) as ObservedPool;
    const observer: PoolObserver = {
      acquire: jest.fn(),
      delivered: jest.fn(),
      release: jest.fn(),
      remove: jest.fn(),
      acquisition: jest.fn(),
    };
    return { pool, observer };
  }

  it('preserves callback receiver, all arguments, return value and release identity', () => {
    const client = {},
      release = jest.fn(),
      receiver = {};
    const callback = jest.fn(function (this: unknown, ...args: unknown[]) {
      expect(this).toBe(receiver);
      expect(args).toEqual([undefined, client, release]);
      return 'callback-result';
    });
    const original = jest.fn(function (this: unknown, cb: unknown) {
      expect(this).toBe(pool);
      return Reflect.apply(cb as Callback, receiver, [
        undefined,
        client,
        release,
      ]);
    });
    const { pool, observer } = fixture(original);
    const restore = observePool(pool, observer);
    const result = diagnosticContext.run({ requestId: 'request-A' }, () =>
      pool.connect(callback),
    );
    expect(result).toBe('callback-result');
    expect(callback).toHaveBeenCalledTimes(1);
    expect(release).not.toHaveBeenCalled();
    expect(observer.delivered).toHaveBeenCalledWith(client, {
      requestId: 'request-A',
    });
    restore();
    expect(pool.connect).toBe(original);
    expect(pool.listenerCount('release')).toBe(0);
  });

  it('does not replace Promise results or attach rejection handlers', () => {
    const promise = Promise.resolve({});
    const then = jest.spyOn(promise, 'then');
    const { pool, observer } = fixture(() => promise);
    const restore = observePool(pool, observer);
    expect(pool.connect()).toBe(promise);
    expect(then).not.toHaveBeenCalled();
    expect(observer.acquisition).not.toHaveBeenCalled();
    restore();
  });

  it('preserves error object/stack and does not count a throwing callback twice', () => {
    const error = new Error('original timeout'),
      stack = error.stack;
    const release = jest.fn();
    const { pool, observer } = fixture((cb) =>
      Reflect.apply(cb as Callback, undefined, [error, undefined, release]),
    );
    const restore = observePool(pool, observer);
    expect(() =>
      pool.connect(
        (received: unknown, _client: unknown, receivedRelease: unknown) => {
          expect(received).toBe(error);
          expect(receivedRelease).toBe(release);
          throw error;
        },
      ),
    ).toThrow(error);
    expect(error.stack).toBe(stack);
    expect(observer.acquisition).toHaveBeenCalledTimes(1);
    restore();
  });

  it('preserves synchronous exceptions from the original connect', () => {
    const error = new Error('synchronous failure');
    const { pool, observer } = fixture(() => {
      throw error;
    });
    const restore = observePool(pool, observer);
    expect(() => pool.connect(jest.fn())).toThrow(error);
    expect(observer.acquisition).toHaveBeenCalledWith(
      expect.any(Number),
      error,
      {},
    );
    restore();
  });

  it('records the waiter context without changing the callback execution context', () => {
    let pending: Callback;
    const { pool, observer } = fixture((callback) => {
      pending = callback as Callback;
    });
    const restore = observePool(pool, observer);
    const callback = jest.fn(() =>
      expect(diagnosticContext.getStore()?.requestId).toBe('releaser'),
    );
    diagnosticContext.run({ requestId: 'waiter' }, () =>
      pool.connect(callback),
    );
    const client = {};
    diagnosticContext.run({ requestId: 'releaser' }, () =>
      pending(undefined, client, jest.fn()),
    );
    expect(observer.delivered).toHaveBeenCalledWith(client, {
      requestId: 'waiter',
    });
    expect(callback).toHaveBeenCalledTimes(1);
    restore();
  });
});
