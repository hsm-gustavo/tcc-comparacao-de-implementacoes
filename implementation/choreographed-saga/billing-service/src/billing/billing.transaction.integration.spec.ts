import { randomUUID } from 'node:crypto';
import { DataSource } from 'typeorm';
import { BillingService } from './billing.service';
import { Payment, PaymentStatus } from './entities/payment.entity';

const integration = process.env.SAGA_TEST_PG_SOCKET ? describe : describe.skip;
integration('BillingService concurrent duplicate events', () => {
  const schema = `billing_idempotency_${process.pid}_${Date.now()}`;
  let db: DataSource;
  beforeAll(async () => {
    db = new DataSource({
      type: 'postgres',
      host: process.env.SAGA_TEST_PG_SOCKET,
      username: 'test',
      password: 'test-only',
      database: 'test',
      schema,
      entities: [Payment],
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
    const repo = db.getRepository(Payment);
    const service = new BillingService(repo);
    const payload = { orderId: randomUUID(), amount: 10 };
    const results = await Promise.all(
      Array.from({ length: 10 }, () => service.processSagaPayment(payload)),
    );
    expect(new Set(results.map((result) => result.id)).size).toBe(1);
    expect(await repo.count()).toBe(1);
    expect(
      results.every((result) => result.status === PaymentStatus.COMPLETED),
    ).toBe(true);
  });
});
