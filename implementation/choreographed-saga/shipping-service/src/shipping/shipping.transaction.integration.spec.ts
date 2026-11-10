import { randomUUID } from 'node:crypto';
import { DataSource } from 'typeorm';
import { ShippingService } from './shipping.service';
import { Shipment, ShippingStatus } from './entities/shipment.entity';

const integration = process.env.SAGA_TEST_PG_SOCKET ? describe : describe.skip;
integration('ShippingService concurrent duplicate events', () => {
  const schema = `shipping_idempotency_${process.pid}_${Date.now()}`;
  let db: DataSource;
  beforeAll(async () => {
    db = new DataSource({
      type: 'postgres',
      host: process.env.SAGA_TEST_PG_SOCKET,
      username: 'test',
      password: 'test-only',
      database: 'test',
      schema,
      entities: [Shipment],
      synchronize: false,
    });
    await db.initialize();
    await db.query(`CREATE SCHEMA "${schema}"`);
    await db.synchronize();
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
  it('persists one completed effect for concurrent redeliveries', async () => {
    const repo = db.getRepository(Shipment);
    const service = new ShippingService(repo);
    const payload = { orderId: randomUUID() };
    const results = await Promise.all(
      Array.from({ length: 10 }, () => service.createSagaShipment(payload)),
    );
    expect(new Set(results.map((result) => result.id)).size).toBe(1);
    expect(await repo.count()).toBe(1);
    expect(
      results.every((result) => result.status === ShippingStatus.SHIPPED),
    ).toBe(true);
  });
});
