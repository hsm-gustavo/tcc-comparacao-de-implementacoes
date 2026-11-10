import { BadRequestException } from '@nestjs/common';
import { RmqContext } from '@nestjs/microservices';
import { MessageRecovery } from './message-recovery';

describe('MessageRecovery', () => {
  function fixture(attempt = 0) {
    const recovery = new MessageRecovery();
    const message = {
      content: Buffer.from(
        '{"pattern":"order.created","data":{"orderId":"one"}}',
      ),
      properties: {
        headers: { 'x-saga-retry': attempt },
        correlationId: 'original',
      },
    };
    const channel = { ack: jest.fn(), nack: jest.fn() };
    const send = jest.fn().mockResolvedValue(undefined);
    const client = jest
      .spyOn(recovery as any, 'client')
      .mockReturnValue({ publishRaw: send });
    return {
      recovery,
      message,
      channel,
      send,
      client,
      context: new RmqContext([message, channel, 'order.created']),
    };
  }
  it.each([
    [0, 1000],
    [1, 5000],
    [2, 30000],
  ])(
    'delays retry %i and ACKs only after confirmation',
    async (attempt, delay) => {
      const f = fixture(attempt);
      f.send.mockImplementation(async () => {
        expect(f.channel.ack).not.toHaveBeenCalled();
      });
      await f.recovery.handle(
        f.context,
        new Error('database temporarily down'),
        'warehouse-saga-queue',
      );
      expect(f.client).toHaveBeenCalledWith(
        `warehouse-saga-queue.retry.${attempt + 1}`,
        {
          durable: true,
          arguments: {
            'x-message-ttl': delay,
            'x-dead-letter-exchange': '',
            'x-dead-letter-routing-key': 'warehouse-saga-queue',
          },
        },
      );
      expect(f.send).toHaveBeenCalledWith(
        f.message.content,
        expect.objectContaining({
          persistent: true,
          correlationId: 'original',
          headers: expect.objectContaining({ 'x-saga-retry': attempt + 1 }),
        }),
      );
      expect(f.channel.ack).toHaveBeenCalledWith(f.message);
    },
  );
  it.each([new BadRequestException('invalid payload'), new Error('exhausted')])(
    'parks invalid or exhausted messages',
    async (error) => {
      const f = fixture(3);
      await f.recovery.handle(f.context, error, 'warehouse-saga-queue');
      expect(f.client).toHaveBeenCalledWith('warehouse-saga-queue.errors', {
        durable: true,
      });
      expect(f.channel.ack).toHaveBeenCalledTimes(1);
      expect(f.channel.nack).not.toHaveBeenCalled();
    },
  );
  it('parks permanent errors on their first delivery', async () => {
    const f = fixture();
    await f.recovery.handle(
      f.context,
      new BadRequestException('invalid'),
      'warehouse-saga-queue',
    );
    expect(f.client).toHaveBeenCalledWith('warehouse-saga-queue.errors', {
      durable: true,
    });
  });
  it('keeps the original recoverable when recovery publication fails', async () => {
    const f = fixture();
    f.send.mockRejectedValue(new Error('broker lost'));
    await f.recovery.handle(
      f.context,
      new Error('database down'),
      'warehouse-saga-queue',
    );
    expect(f.channel.ack).not.toHaveBeenCalled();
    expect(f.channel.nack).toHaveBeenCalledWith(f.message, false, true);
  });
});
