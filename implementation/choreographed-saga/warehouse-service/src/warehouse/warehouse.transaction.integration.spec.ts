import { randomUUID } from 'node:crypto';
import { DataSource } from 'typeorm';
import { WarehouseService } from './warehouse.service';
import { Product } from './entities/product.entity';
import { StockReservation } from './entities/stock-reservation.entity';

const integration = process.env.SAGA_TEST_PG_SOCKET ? describe : describe.skip;
integration('Warehouse concurrent idempotency in PostgreSQL', () => {
  const schema = `warehouse_atomicity_${process.pid}_${Date.now()}`;
  let db: DataSource;
  let service: WarehouseService;
  beforeAll(async () => {
    db = new DataSource({
      type: 'postgres',
      host: process.env.SAGA_TEST_PG_SOCKET,
      username: 'test',
      password: 'test-only',
      database: 'test',
      schema,
      entities: [Product, StockReservation],
      synchronize: false,
    });
    await db.initialize();
    await db.query(`CREATE SCHEMA "${schema}"`);
    await db.synchronize();
    service = new WarehouseService(
      db.getRepository(Product),
      db.getRepository(StockReservation),
    );
  });
  afterAll(async () => {
    if (db?.isInitialized) {
      try {
        await db.query(`DROP SCHEMA "${schema}" CASCADE`);
      } finally {
        await db.destroy();
      }
    }
  });
  it('deducts and restores stock only once for concurrent duplicates', async () => {
    const product = await service.create({
      name: 'test',
      price: 10,
      stockQuantity: 100,
    });
    const payload = {
      orderId: randomUUID(),
      productId: product.id,
      quantity: 5,
    };
    await Promise.all(
      Array.from({ length: 10 }, () => service.reserveForSaga(payload)),
    );
    expect((await service.findOne(product.id)).stockQuantity).toBe(95);
    expect(await db.getRepository(StockReservation).count()).toBe(1);
    await Promise.all(
      Array.from({ length: 10 }, () =>
        service.releaseReservation(payload.orderId),
      ),
    );
    expect((await service.findOne(product.id)).stockQuantity).toBe(100);
  });
  it('serializes competing orders without lost stock updates', async () => {
    const product = await service.create({
      name: 'test',
      price: 10,
      stockQuantity: 5,
    });
    const results = await Promise.allSettled(
      Array.from({ length: 10 }, () =>
        service.reserveForSaga({
          orderId: randomUUID(),
          productId: product.id,
          quantity: 1,
        }),
      ),
    );
    expect(
      results.filter((result) => result.status === 'fulfilled'),
    ).toHaveLength(5);
    expect((await service.findOne(product.id)).stockQuantity).toBe(0);
  });
  it('rolls back the stock deduction if reservation persistence fails', async () => {
    const product = await service.create({
      name: 'test',
      price: 10,
      stockQuantity: 100,
    });
    await db.query(
      `ALTER TABLE "${schema}".stock_reservations_saga ADD CONSTRAINT test_failure CHECK (quantity <> 7) NOT VALID`,
    );
    await expect(
      service.reserveForSaga({
        orderId: randomUUID(),
        productId: product.id,
        quantity: 7,
      }),
    ).rejects.toThrow('test_failure');
    expect((await service.findOne(product.id)).stockQuantity).toBe(100);
  });
});
