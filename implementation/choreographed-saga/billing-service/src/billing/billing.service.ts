import { BadRequestException, Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Payment, PaymentStatus } from './entities/payment.entity';
import { Repository } from 'typeorm';
import { ProcessPaymentDto } from './dto/process-payment.dto';
import { SagaPublisher } from '../saga-publisher';

type BillingSagaPayload = {
  orderId: string;
  customerId?: string;
  productId?: string;
  quantity?: number;
  amount: number;
  reason?: string;
};

@Injectable()
export class BillingService extends SagaPublisher {
  private readonly shippingQueue = 'shipping-saga-queue';
  private readonly orderQueue = 'order-saga-queue';
  private readonly warehouseQueue = 'warehouse-saga-queue';

  constructor(
    @InjectRepository(Payment) private paymentRepository: Repository<Payment>,
  ) {
    super();
  }

  async processPayment(dto: ProcessPaymentDto) {
    const payment = this.paymentRepository.create({
      orderId: dto.orderId,
      amount: dto.amount,
      status: PaymentStatus.PENDING,
    });

    await this.paymentRepository.save(payment);

    /* //20% chance of failure for later testing
    const isSuccessful = Math.random() < 0.2;

    if (!isSuccessful) {
      payment.status = PaymentStatus.FAILED;
      await this.paymentRepository.save(payment);
      throw new BadRequestException('Payment declined by the bank');
    } */

    payment.status = PaymentStatus.COMPLETED;
    return this.paymentRepository.save(payment);
  }

  async processSagaPayment(payload: BillingSagaPayload) {
    return this.paymentRepository.manager.transaction(async (manager) => {
      await manager.query(
        'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
        [payload.orderId],
      );
      const existing = await manager.findOneBy(Payment, {
        orderId: payload.orderId,
      });
      if (existing && existing.status !== PaymentStatus.PENDING)
        return existing;
      if (!Number.isFinite(payload.amount) || payload.amount <= 0) {
        throw new BadRequestException(
          'Payment amount must be greater than zero',
        );
      }
      const payment =
        existing ??
        manager.create(Payment, {
          orderId: payload.orderId,
          amount: payload.amount,
        });
      payment.status = PaymentStatus.COMPLETED;
      return manager.save(Payment, payment);
    });
  }

  async refundPayment(orderId: string) {
    return this.paymentRepository.manager.transaction(async (manager) => {
      await manager.query(
        'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
        [orderId],
      );
      const payment = await manager.findOneBy(Payment, { orderId });
      if (payment && payment.status === PaymentStatus.COMPLETED) {
        payment.status = PaymentStatus.REFUNDED;
        await manager.save(Payment, payment);
      }
    });
  }

  async onStockReserved(payload: BillingSagaPayload) {
    let payment: Payment;
    try {
      payment = await this.processSagaPayment(payload);
    } catch (error) {
      if (!(error instanceof BadRequestException)) throw error;
      await Promise.all([
        this.emit(this.warehouseQueue, 'billing.payment_failed', {
          ...payload,
          reason: String(error),
        }),
        this.emit(this.orderQueue, 'billing.payment_failed', {
          ...payload,
          reason: String(error),
        }),
      ]);
      return;
    }
    if (payment.status === PaymentStatus.COMPLETED) {
      await this.emit(this.shippingQueue, 'billing.payment_confirmed', payload);
    }
    return payment;
  }

  async onShippingFailed(payload: BillingSagaPayload) {
    return this.refundPayment(payload.orderId);
  }

  async findOne(orderId: string) {
    return this.paymentRepository.findOneBy({
      orderId: orderId,
    });
  }
}
