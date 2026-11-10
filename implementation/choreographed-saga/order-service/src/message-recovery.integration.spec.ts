import { randomUUID } from 'node:crypto';
import { RmqContext } from '@nestjs/microservices';
import { MessageRecovery } from './message-recovery';
import { SagaPublisher } from './saga-publisher';

// Explicit opt-in; use only the disposable RabbitMQ created for verification.
const integration = process.env.SAGA_TEST_RABBIT_URL ? describe : describe.skip;
integration('RabbitMQ recovery confirmations and delayed delivery', () => {
  const queue = `saga-check-${randomUUID()}`;
  let connection: any;
  let channel: any;
  let recovery: MessageRecovery;
  let publisher: SagaPublisher;
  let previousUrl: string | undefined;
  beforeAll(async () => {
    previousUrl = process.env.RABBITMQ_URL;
    process.env.RABBITMQ_URL = process.env.SAGA_TEST_RABBIT_URL;
    const amqp = require('amqplib');
    connection = await amqp.connect(process.env.SAGA_TEST_RABBIT_URL);
    channel = await connection.createConfirmChannel();
    await channel.assertQueue(queue, { durable: true });
    recovery = new MessageRecovery();
    publisher = new SagaPublisher();
  });
  afterAll(async () => {
    await publisher?.onModuleDestroy();
    await recovery?.onModuleDestroy();
    if (channel) {
      for (const name of [queue, `${queue}.retry.1`, `${queue}.errors`])
        await channel.deleteQueue(name);
      await channel.close();
    }
    await connection?.close();
    if (previousUrl === undefined) delete process.env.RABBITMQ_URL;
    else process.env.RABBITMQ_URL = previousUrl;
  });
  async function get(name: string) {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      const message = await channel.get(name, { noAck: false });
      if (message) return message;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    throw new Error('Message did not arrive: ' + name);
  }
  it('preserves bytes across retry, then parks an exhausted delivery', async () => {
    const content = Buffer.from(
      JSON.stringify({ pattern: 'order.created', data: { orderId: 'one' } }),
    );
    channel.sendToQueue(queue, content, { persistent: true });
    await channel.waitForConfirms();
    const first = await get(queue);
    const start = Date.now();
    await recovery.handle(
      new RmqContext([first, channel, 'order.created']),
      new Error('temporary database failure'),
      queue,
    );
    const retried = await get(queue);
    expect(Date.now() - start).toBeGreaterThanOrEqual(900);
    expect(retried.content.equals(content)).toBe(true);
    expect(retried.properties.headers['x-saga-retry']).toBe(1);
    retried.properties.headers['x-saga-retry'] = 3;
    await recovery.handle(
      new RmqContext([retried, channel, 'order.created']),
      new Error('exhausted'),
      queue,
    );
    const parked = await get(`${queue}.errors`);
    expect(parked.content.equals(content)).toBe(true);
    expect(parked.properties.headers['x-saga-error']).toContain('exhausted');
    channel.ack(parked);
  }, 20000);
});
