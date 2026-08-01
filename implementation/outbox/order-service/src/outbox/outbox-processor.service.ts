// outbox-processor.service.ts
import { Injectable, Logger } from '@nestjs/common';
import { Cron, CronExpression } from '@nestjs/schedule';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Outbox, OutboxStatus } from './entities/outbox.entity';
import {
  ClientProxyFactory,
  Transport,
  ClientProxy,
} from '@nestjs/microservices';

@Injectable()
export class OutboxProcessorService {
  private readonly logger = new Logger(OutboxProcessorService.name);
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';
  private clientsMap: Map<string, ClientProxy> = new Map();

  constructor(
    @InjectRepository(Outbox)
    private readonly outboxRepo: Repository<Outbox>,
  ) {}

  private getClient(queue: string): ClientProxy {
    if (!this.clientsMap.has(queue)) {
      const client = ClientProxyFactory.create({
        transport: Transport.RMQ,
        options: {
          urls: [this.rabbitUrl],
          queue,
          queueOptions: { durable: true },
        },
      });
      this.clientsMap.set(queue, client);
    }
    return this.clientsMap.get(queue)!;
  }

  @Cron(CronExpression.EVERY_5_SECONDS)
  async processOutbox() {
    const pendingEntries = await this.outboxRepo.find({
      where: { status: OutboxStatus.PENDING },
      order: { createdAt: 'ASC' },
      take: 20,
    });

    if (pendingEntries.length === 0) return;

    this.logger.log(
      `Encontradas ${pendingEntries.length} mensagens pendentes no Outbox.`,
    );

    for (const entry of pendingEntries) {
      const client = this.getClient(entry.queue);

      try {
        await new Promise<void>((resolve, reject) => {
          client.emit(entry.pattern, entry.payload).subscribe({
            complete: resolve,
            error: reject,
          });
        });

        entry.status = OutboxStatus.PROCESSED;
        entry.processedAt = new Date();
        await this.outboxRepo.save(entry);
      } catch (error) {
        this.logger.error(
          `Falha ao processar registro outbox ${entry.id}:`,
          error,
        );

        entry.status = OutboxStatus.FAILED;
        await this.outboxRepo.save(entry);
      }
    }
  }
}
