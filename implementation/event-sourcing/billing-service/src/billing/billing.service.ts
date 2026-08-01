import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import {
  ClientProxy,
  ClientProxyFactory,
  Transport,
} from '@nestjs/microservices';
import { BillingEvent } from './entities/billing-event.entity';
import { Payment, PaymentStatus } from './entities/payment.entity';

@Injectable()
export class BillingService {
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';
  private readonly shippingQueue = 'shipping-service-queue';

  constructor(
    @InjectRepository(BillingEvent)
    private readonly eventRepository: Repository<BillingEvent>,
    @InjectRepository(Payment)
    private readonly paymentRepository: Repository<Payment>,
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
    payload: Record<string, unknown>,
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

  async processPaymentFromEvent(orderId: string, amount: number) {
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

    await this.emit(this.shippingQueue, 'billing.payment_completed', {
      orderId,
      amount,
    });

    return { orderId, amount, status: 'COMPLETED' };
  }

  async refundPayment(orderId: string) {
    const state = await this.rebuildBillingState(orderId);

    if (!state.exists) {
      throw new NotFoundException('No billing records found for this order');
    }

    if (state.status !== 'COMPLETED') {
      throw new BadRequestException(
        `Cannot refund payment in status: ${state.status}`,
      );
    }

    const refundEvent = this.eventRepository.create({
      aggregateId: orderId,
      type: 'PaymentRefunded',
      data: { amount: state.amount, refundReason: 'User requested refund' },
    });

    await this.eventRepository.save(refundEvent);
    return { orderId, status: 'REFUNDED' };
  }

  async findOne(orderId: string) {
    const state = await this.rebuildBillingState(orderId);
    if (!state.exists) throw new NotFoundException('Billing record not found');
    return state;
  }

  private async rebuildBillingState(orderId: string) {
    const events = await this.eventRepository.find({
      where: { aggregateId: orderId },
      order: { createdAt: 'ASC' },
    });

    if (events.length === 0) {
      return { exists: false, orderId, status: 'NONE', amount: 0 };
    }

    let status = 'PENDING';
    let amount = 0;

    for (const event of events) {
      switch (event.type) {
        case 'PaymentPending':
          status = 'PENDING';
          amount = event.data.amount || 0;
          break;
        case 'PaymentCompleted':
          status = 'COMPLETED';
          break;
        case 'PaymentFailed':
          status = 'FAILED';
          break;
        case 'PaymentRefunded':
          status = 'REFUNDED';
          break;
      }
    }

    return {
      exists: true,
      orderId,
      status,
      amount,
    };
  }

  async replayEvents() {
    const events = await this.eventRepository.find({
      order: { createdAt: 'ASC' },
    });

    let processedCount = 0;

    for (const event of events) {
      const orderId = event.aggregateId; //

      if (event.type === 'PaymentPending') {
        let payment = await this.paymentRepository.findOneBy({ orderId });

        if (!payment) {
          payment = this.paymentRepository.create({
            orderId: orderId,
            amount: event.data.amount,
            status: PaymentStatus.PENDING,
          });
        } else {
          payment.amount =
            event.data.amount !== undefined
              ? event.data.amount
              : payment.amount;
        }
        await this.paymentRepository.save(payment);
        processedCount++;
      }

      if (event.type === 'PaymentCompleted') {
        const payment = await this.paymentRepository.findOneBy({ orderId });
        if (payment) {
          payment.status = PaymentStatus.COMPLETED;
          await this.paymentRepository.save(payment);
          processedCount++;
        }
      }

      if (event.type === 'PaymentFailed') {
        const payment = await this.paymentRepository.findOneBy({ orderId });
        if (payment) {
          payment.status = PaymentStatus.FAILED;
          await this.paymentRepository.save(payment);
          processedCount++;
        }
      }

      if (event.type === 'PaymentRefunded') {
        const payment = await this.paymentRepository.findOneBy({ orderId });
        if (payment) {
          payment.status = PaymentStatus.REFUNDED;
          await this.paymentRepository.save(payment);
          processedCount++;
        }
      }
    }

    return {
      success: true,
      message: `Replay finalizado. ${processedCount} eventos de pagamentos processados.`,
    };
  }
}
