// billing.command.service.ts
import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { BillingEvent } from './entities/billing-event.entity';
import {
  ClientProxy,
  ClientProxyFactory,
  Transport,
} from '@nestjs/microservices';

@Injectable()
export class BillingCommandService {
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';
  private readonly billingProjectionQueue = 'billing-projection-queue';
  private readonly shippingQueue = 'shipping-service-queue';

  constructor(
    @InjectRepository(BillingEvent)
    private readonly eventRepository: Repository<BillingEvent>,
  ) {}

  private createClient(queue: string): ClientProxy {
    return ClientProxyFactory.create({
      transport: Transport.RMQ,
      options: {
        urls: [this.rabbitUrl],
        queue,
        queueOptions: { durable: true },
      },
    });
  }

  private async emit(
    queue: string,
    pattern: string,
    payload: BillingEvent | Record<string, unknown>,
  ) {
    const client = this.createClient(queue);

    try {
      await new Promise<void>((resolve, reject) => {
        client.emit(pattern, payload).subscribe({
          complete: resolve,
          error: reject,
        });
      });
    } finally {
      await client.close();
    }
  }

  async processPayment(orderId: string, amount: number) {
    const pendingEvent = this.eventRepository.create({
      aggregateId: orderId,
      type: 'PaymentPending',
      data: { amount },
    });
    await this.eventRepository.save(pendingEvent);

    const completedEvent = this.eventRepository.create({
      aggregateId: orderId,
      type: 'PaymentCompleted',
      data: { amount },
    });
    await this.eventRepository.save(completedEvent);

    await this.emit(this.billingProjectionQueue, 'billing.payment_completed', {
      orderId,
      amount,
    });

    await this.emit(this.shippingQueue, 'billing.payment_completed', {
      orderId,
      amount,
    });

    return { orderId, amount, status: 'COMPLETED' };
  }

  async processPaymentFailed(orderId: string, amount: number) {
    const failedEvent = this.eventRepository.create({
      aggregateId: orderId,
      type: 'PaymentFailed',
      data: { amount, reason: 'Payment integration failed' },
    });
    await this.eventRepository.save(failedEvent);
    await this.emit(this.billingProjectionQueue, 'billing.payment_failed', {
      orderId,
      amount,
      reason: 'Payment integration failed',
    });
  }

  async refundPayment(orderId: string, amount: number) {
    const refundEvent = this.eventRepository.create({
      aggregateId: orderId,
      type: 'PaymentRefunded',
      data: { amount, refundReason: 'User requested refund' },
    });
    return this.eventRepository.save(refundEvent);
  }
}
