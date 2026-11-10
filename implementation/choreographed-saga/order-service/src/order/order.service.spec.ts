import { Repository } from 'typeorm';
import { OrderService } from './order.service';
import { Order, OrderStatus } from './entities/order.entity';
import { OrderItem } from './entities/order-item.entity';

describe('OrderService local transaction', () => {
  const dto = {
    customerId: 'customer',
    productId: 'product',
    quantity: 1,
    amount: 10,
  };
  function fixture(failItem = false) {
    let transactionOpen = false;
    const manager = {
      create: jest.fn((entity, values) => Object.assign(new entity(), values)),
      save: jest.fn(async (entity, value) => {
        expect(transactionOpen).toBe(true);
        if (entity === OrderItem && failItem)
          throw new Error('item insert failed');
        return entity === Order
          ? { ...value, id: 'order-id', status: OrderStatus.PENDING }
          : value;
      }),
    };
    const transaction = jest.fn(async (work) => {
      transactionOpen = true;
      try {
        return await work(manager);
      } finally {
        transactionOpen = false;
      }
    });
    const repo = {
      manager: { transaction },
      save: jest.fn().mockImplementation(async (value) => value),
    };
    const service = new OrderService(repo as unknown as Repository<Order>);
    const emit = jest
      .spyOn(service as any, 'emit')
      .mockImplementation(async () => {
        expect(transactionOpen).toBe(false);
      });
    return { service, manager, transaction, repo, emit };
  }

  it('saves both records with the transaction manager and publishes only after commit', async () => {
    const f = fixture();
    const result = await f.service.createOrder(dto);
    expect(f.transaction).toHaveBeenCalledTimes(1);
    expect(f.manager.save.mock.calls.map((call) => call[0])).toEqual([
      Order,
      OrderItem,
    ]);
    expect(f.repo.save).not.toHaveBeenCalled();
    expect(f.emit).toHaveBeenCalledWith(
      'warehouse-saga-queue',
      'order.created',
      expect.objectContaining({ orderId: result.id }),
    );
  });

  it('propagates item failure to the transaction without publishing an event', async () => {
    const f = fixture(true);
    await expect(f.service.createOrder(dto)).rejects.toThrow(
      'item insert failed',
    );
    expect(f.emit).not.toHaveBeenCalled();
    expect(f.repo.save).not.toHaveBeenCalled();
  });

  it('does not mark the order cancelled when publication outcome is uncertain', async () => {
    const f = fixture();
    f.emit.mockRejectedValueOnce(new Error('broker failed'));
    await expect(f.service.createOrder(dto)).rejects.toThrow('broker failed');
    expect(f.repo.save).not.toHaveBeenCalled();
  });
});
