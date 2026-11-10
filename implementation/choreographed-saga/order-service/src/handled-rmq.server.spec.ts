import { EventEmitter } from 'node:events';
import { RmqContext, ServerRMQ } from '@nestjs/microservices';
import { HandledRmqServer } from './handled-rmq.server';

describe('HandledRmqServer', () => {
  it('parks unknown event patterns instead of silently discarding them', async () => {
    const server = new HandledRmqServer({ queue: 'test-queue', noAck: false });
    const recover = jest
      .spyOn((server as any).recovery, 'handle')
      .mockResolvedValue(undefined);
    const context = new RmqContext([{}, {}, 'unknown']);
    await server.handleEvent(
      'unknown',
      { pattern: 'unknown', data: {} },
      context,
    );
    expect(recover).toHaveBeenCalledWith(
      context,
      expect.any(Error),
      'test-queue',
    );
  });
  it('handles errors on every consumer wrapper without changing factory arguments', () => {
    const wrappers = [new EventEmitter(), new EventEmitter()];
    const manager = {
      createChannel: jest
        .fn()
        .mockReturnValueOnce(wrappers[0])
        .mockReturnValueOnce(wrappers[1]),
    };
    const createChannel = manager.createChannel;
    const factory = jest
      .spyOn(ServerRMQ.prototype, 'createClient')
      .mockReturnValue(manager);
    try {
      const server = new HandledRmqServer({
        queue: 'test-queue',
        noAck: false,
      });
      const log = jest.fn();
      (server as any).logger = { error: log };
      const connection = server.createClient();
      const options = { setup: jest.fn(), json: false };
      expect(connection.createChannel(options)).toBe(wrappers[0]);
      expect(connection.createChannel(options)).toBe(wrappers[1]);
      for (const wrapper of wrappers) {
        const error = new Error('Channel ended, no reply will be forthcoming');
        expect(() => wrapper.emit('error', error)).not.toThrow();
        expect(log).toHaveBeenCalledWith(error);
      }
      expect(log).toHaveBeenCalledTimes(2);
      expect(createChannel.mock.calls).toEqual([[options], [options]]);
    } finally {
      factory.mockRestore();
    }
  });
});
