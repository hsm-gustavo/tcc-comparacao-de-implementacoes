import { OnModuleDestroy } from '@nestjs/common';
import { RmqRecordBuilder } from '@nestjs/microservices';
import { HandledRmqClient } from './handled-rmq.client';

/** One lazily connected client per destination, shared by concurrent emits. */
export class SagaPublisher implements OnModuleDestroy {
  private readonly clients = new Map<string, HandledRmqClient>();

  protected client(
    queue: string,
    queueOptions: Record<string, unknown> = { durable: true },
  ) {
    let client = this.clients.get(queue);
    if (!client) {
      client = new HandledRmqClient({
        urls: [process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672'],
        queue,
        queueOptions,
      });
      this.clients.set(queue, client);
    }
    return client;
  }

  protected async emit(queue: string, pattern: string, payload: unknown) {
    const record = new RmqRecordBuilder(payload)
      .setOptions({ persistent: true })
      .build();
    await new Promise<void>((resolve, reject) => {
      this.client(queue)
        .emit(pattern, record)
        .subscribe({ complete: resolve, error: reject });
    });
  }

  async onModuleDestroy() {
    await Promise.all(
      [...this.clients.values()].map((client) => client.close()),
    );
    this.clients.clear();
  }
}
