import { SagaPublisher } from './saga-publisher';
import { HandledRmqClient } from './handled-rmq.client';
import { EMPTY, throwError } from 'rxjs';

class Publisher extends SagaPublisher {
  send(queue: string) {
    return this.emit(queue, 'test.event', { orderId: 'one' });
  }
}

describe('SagaPublisher', () => {
  afterEach(() => jest.restoreAllMocks());
  it('shares clients across concurrent publishes and closes only at shutdown', async () => {
    const instances = new Set<HandledRmqClient>();
    const emit = jest
      .spyOn(HandledRmqClient.prototype, 'emit')
      .mockImplementation(function (this: HandledRmqClient) {
        instances.add(this);
        return EMPTY;
      });
    const close = jest
      .spyOn(HandledRmqClient.prototype, 'close')
      .mockResolvedValue(undefined);
    const publisher = new Publisher();
    await Promise.all(
      Array.from({ length: 20 }, () => publisher.send('warehouse-saga-queue')),
    );
    expect(instances.size).toBe(1);
    await publisher.send('billing-saga-queue');
    expect(instances.size).toBe(2);
    expect(close).not.toHaveBeenCalled();
    expect(emit).toHaveBeenCalledWith(
      'test.event',
      expect.objectContaining({ options: { persistent: true } }),
    );
    await publisher.onModuleDestroy();
    expect(close).toHaveBeenCalledTimes(2);
  });
  it('propagates publication errors without closing concurrent publishers', async () => {
    jest
      .spyOn(HandledRmqClient.prototype, 'emit')
      .mockReturnValue(throwError(() => new Error('publish failed')));
    const close = jest
      .spyOn(HandledRmqClient.prototype, 'close')
      .mockResolvedValue(undefined);
    const publisher = new Publisher();
    await expect(publisher.send('queue')).rejects.toThrow('publish failed');
    expect(close).not.toHaveBeenCalled();
    await publisher.onModuleDestroy();
  });
});
