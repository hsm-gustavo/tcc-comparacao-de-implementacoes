import { ClientRMQ } from '@nestjs/microservices';
import { EventEmitter } from 'node:events';
import { HandledRmqClient } from './handled-rmq.client';

describe('HandledRmqClient', () => {
  it('shares initial connection failure and permits a fresh connection afterwards', async () => {
    const original = new Error('setup failed');
    const connect = jest
      .spyOn(ClientRMQ.prototype, 'connect')
      .mockRejectedValueOnce(original)
      .mockResolvedValue(undefined);
    const close = jest
      .spyOn(ClientRMQ.prototype, 'close')
      .mockResolvedValue(undefined);
    try {
      const client = new HandledRmqClient({ queue: 'test' });
      const first = client.connect();
      expect(client.connect()).toBe(first);
      await expect(first).rejects.toBe(original);
      expect(close).toHaveBeenCalledTimes(1);
      await client.connect();
      expect(connect).toHaveBeenCalledTimes(2);
    } finally {
      connect.mockRestore();
      close.mockRestore();
    }
  });

  function fixture() {
    const client = new HandledRmqClient({ queue: 'test-queue' });
    const wrappers: Array<EventEmitter & { close: jest.Mock }> = [];
    const setups: Array<(channel: unknown) => Promise<void>> = [];
    const manager = {
      createChannel: jest.fn((options) => {
        const wrapper = Object.assign(new EventEmitter(), {
          close: jest.fn().mockResolvedValue(undefined),
        });
        wrappers.push(wrapper);
        setups.push(options.setup);
        return wrapper;
      }),
      close: jest.fn().mockResolvedValue(undefined),
    };
    const log = jest.fn();
    (client as any).client = manager;
    (client as any).logger = { error: log };
    const channel = {
      assertQueue: jest.fn().mockResolvedValue(undefined),
      prefetch: jest.fn().mockResolvedValue(undefined),
      consume: jest.fn().mockResolvedValue(undefined),
    };
    return { client, wrappers, setups, channel, log, manager };
  }

  it('rejects channel setup failure with the original error and closes resources', async () => {
    const f = fixture();
    const ready = f.client.createChannel();
    const error = new Error('Channel ended, no reply will be forthcoming');
    expect(() => f.wrappers[0].emit('error', error)).not.toThrow();
    await expect(ready).rejects.toBe(error);
    expect(f.log).toHaveBeenCalledWith(error);
    await f.client.close();
    expect(f.wrappers[0].close).toHaveBeenCalledTimes(1);
    expect(f.manager.close).toHaveBeenCalledTimes(1);
  });

  it('handles late error events without crashing after setup succeeded', async () => {
    const f = fixture();
    const ready = f.client.createChannel();
    await f.setups[0](f.channel);
    await ready;
    const error = new Error('late channel error');
    expect(() => f.wrappers[0].emit('error', error)).not.toThrow();
    expect(f.log).toHaveBeenCalledWith(error);
    await f.client.close();
  });

  it('handles errors on the initial channel even if Nest replaces its promise', async () => {
    const f = fixture();
    void f.client.createChannel();
    const ready = f.client.createChannel();
    f.wrappers[0].emit('error', new Error('initial channel failed'));
    await f.setups[1](f.channel);
    await ready;
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(f.log).toHaveBeenCalledTimes(1);
    await f.client.close();
  });
});
