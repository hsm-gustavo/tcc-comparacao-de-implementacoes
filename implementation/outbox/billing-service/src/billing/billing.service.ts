import { BadRequestException, Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Payment, PaymentStatus } from './entities/payment.entity';
import { EntityManager, Repository } from 'typeorm';
import { ProcessPaymentDto } from './dto/process-payment.dto';
import { OutboxService } from 'src/outbox/outbox.service';

type BillingSagaPayload = {
  orderId: string;
  customerId?: string;
  productId?: string;
  quantity?: number;
  amount: number;
  reason?: string;
};

@Injectable()
export class BillingService {
  private readonly shippingQueue = 'shipping-saga-queue';
  private readonly orderQueue = 'order-saga-queue';
  private readonly warehouseQueue = 'warehouse-saga-queue';

  constructor(
    @InjectRepository(Payment) private paymentRepository: Repository<Payment>,
    private readonly outboxService: OutboxService,
  ) {}

  async processPayment(dto: ProcessPaymentDto) {
    return this.paymentRepository.manager.transaction(
      async (transactionalEntityManager) => {
        const payment = transactionalEntityManager.create(Payment, {
          orderId: dto.orderId,
          amount: dto.amount,
          status: PaymentStatus.PENDING,
        });

        await transactionalEntityManager.save(Payment, payment);

        /* //20% chance of failure for later testing
    const isSuccessful = Math.random() < 0.2;

    if (!isSuccessful) {
      payment.status = PaymentStatus.FAILED;
      await transactionalEntityManager.save(Payment, payment);
      throw new BadRequestException('Payment declined by the bank');
    } */

        payment.status = PaymentStatus.COMPLETED;
        return transactionalEntityManager.save(Payment, payment);
      },
    );
  }

  async processSagaPayment(
    payload: BillingSagaPayload,
    manager: EntityManager,
  ) {
    const existingPayment = await manager.findOneBy(Payment, {
      orderId: payload.orderId,
    });

    if (existingPayment) {
      return existingPayment;
    }

    if (payload.amount <= 0) {
      throw new BadRequestException('Payment amount must be greater than zero');
    }

    const payment = manager.create(Payment, {
      orderId: payload.orderId,
      amount: payload.amount,
      status: PaymentStatus.PENDING,
    });

    await manager.save(Payment, payment);

    payment.status = PaymentStatus.COMPLETED;
    return manager.save(Payment, payment);
  }

  async refundPayment(orderId: string, manager?: EntityManager) {
    const repo = manager
      ? manager.getRepository(Payment)
      : this.paymentRepository;
    const payment = await repo.findOneBy({
      orderId: orderId,
    });

    if (payment && payment.status === PaymentStatus.COMPLETED) {
      payment.status = PaymentStatus.REFUNDED;
      await repo.save(payment);
    }
  }

  async onStockReserved(payload: BillingSagaPayload) {
    return this.paymentRepository.manager.transaction(
      async (transactionalEntityManager) => {
        try {
          const payment = await this.processSagaPayment(
            payload,
            transactionalEntityManager,
          );

          await this.outboxService.put(
            this.shippingQueue,
            'billing.payment_confirmed',
            {
              orderId: payload.orderId,
              customerId: payload.customerId,
              productId: payload.productId,
              quantity: payload.quantity,
              amount: payload.amount,
            },
            transactionalEntityManager,
          );

          return payment;
        } catch (error) {
          await this.outboxService.put(
            this.warehouseQueue,
            'billing.payment_failed',
            {
              orderId: payload.orderId,
              customerId: payload.customerId,
              productId: payload.productId,
              quantity: payload.quantity,
              amount: payload.amount,
              reason: String(error),
            },
            transactionalEntityManager,
          );
          await this.outboxService.put(
            this.orderQueue,
            'billing.payment_failed',
            {
              orderId: payload.orderId,
              customerId: payload.customerId,
              productId: payload.productId,
              quantity: payload.quantity,
              amount: payload.amount,
              reason: String(error),
            },
            transactionalEntityManager,
          );

          throw error;
        }
      },
    );
  }

  async onShippingFailed(payload: BillingSagaPayload) {
    return this.paymentRepository.manager.transaction(
      async (transactionalEntityManager) => {
        await this.refundPayment(payload.orderId, transactionalEntityManager);
      },
    );
  }

  async findOne(orderId: string) {
    return this.paymentRepository.findOneBy({
      orderId: orderId,
    });
  }
}
