import { DataSource } from 'typeorm';
import { OrderService } from './order.service';
import { Order } from './entities/order.entity';
import { OrderItem } from './entities/order-item.entity';

// Opt-in: use only a disposable PostgreSQL database named test, via Unix socket.
const integration = process.env.SAGA_TEST_PG_SOCKET ? describe : describe.skip;

integration('OrderService PostgreSQL atomicity', () => {
  const schema = `saga_atomicity_${process.pid}_${Date.now()}`;
  let dataSource: DataSource;

  beforeAll(async () => {
    dataSource = new DataSource({
      type: 'postgres',
      host: process.env.SAGA_TEST_PG_SOCKET,
      username: 'test',
      password: 'test-only',
      database: 'test',
      schema,
      entities: [Order, OrderItem],
      synchronize: false,
    });
    await dataSource.initialize();
    await dataSource.query(`CREATE SCHEMA "${schema}"`);
    await dataSource.synchronize();
    await dataSource.query(`ALTER TABLE "${schema}".order_items
      ADD CONSTRAINT positive_quantity CHECK (quantity > 0)`);
  });

  afterAll(async () => {
    if (dataSource?.isInitialized) {
      try {
        await dataSource.query(`DROP SCHEMA "${schema}" CASCADE`);
      } finally {
        await dataSource.destroy();
      }
    }
  });

  it('commits both records before publication and rolls both back if item insert fails', async () => {
    const orders = dataSource.getRepository(Order);
    const items = dataSource.getRepository(OrderItem);
    const service = new OrderService(orders);
    const publish = jest
      .spyOn(service as any, 'emit')
      .mockImplementation(async () => {
        // These independent queries can see the records only after local commit.
        expect(await orders.count()).toBe(1);
        expect(await items.count()).toBe(1);
      });
    const dto = {
      customerId: 'integration',
      productId: 'product',
      quantity: 1,
      amount: 10,
    };
    await service.createOrder(dto);
    await expect(service.createOrder({ ...dto, quantity: 0 })).rejects.toThrow(
      'positive_quantity',
    );
    expect(await orders.count()).toBe(1);
    expect(await items.count()).toBe(1);
    expect(publish).toHaveBeenCalledTimes(1);
  });
});
