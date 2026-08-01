import { Injectable } from '@nestjs/common';
import { EntityManager } from 'typeorm';
import { Outbox, OutboxStatus } from './entities/outbox.entity';

@Injectable()
export class OutboxService {
  async put(
    queue: string,
    pattern: string,
    payload: Record<string, unknown>,
    entityManager: EntityManager,
  ): Promise<Outbox> {
    const outboxEntry = entityManager.create(Outbox, {
      queue,
      pattern,
      payload,
      status: OutboxStatus.PENDING,
    });

    return entityManager.save(Outbox, outboxEntry);
  }
}
