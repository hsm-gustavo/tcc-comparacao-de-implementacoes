import { BadRequestException, Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Payment, PaymentStatus } from './entities/payment.entity';
import { Repository } from 'typeorm';
import { ProcessPaymentDto } from './dto/process-payment.dto';
import {
  ClientProxy,
  ClientProxyFactory,
  Transport,
} from '@nestjs/microservices';

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
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';
  private readonly shippingQueue = 'shipping-saga-queue';
  private readonly orderQueue = 'order-saga-queue';
  private readonly warehouseQueue = 'warehouse-saga-queue';

  constructor(
    @InjectRepository(Payment) private paymentRepository: Repository<Payment>,
  ) {}

  private createClient(queue: string): ClientProxy {
    return ClientProxyFactory.create({
      transport: Transport.RMQ,
      options: {
        urls: [this.rabbitUrl],
        queue,
        queueOptions: {
          durable: true,
        },
      },
    });
  }

  private async emit(
    queue: string,
    pattern: string,
    payload: BillingSagaPayload,
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
    const existingPayment = await this.paymentRepository.findOneBy({
      orderId: payload.orderId,
    });

    if (existingPayment) {
      return existingPayment;
    }

    if (payload.amount <= 0) {
      throw new BadRequestException('Payment amount must be greater than zero');
    }

    const payment = this.paymentRepository.create({
      orderId: payload.orderId,
      amount: payload.amount,
      status: PaymentStatus.PENDING,
    });

    await this.paymentRepository.save(payment);

    payment.status = PaymentStatus.COMPLETED;
    return this.paymentRepository.save(payment);
  }

  async refundPayment(orderId: string) {
    const payment = await this.paymentRepository.findOneBy({
      orderId: orderId,
    });

    if (payment && payment.status === PaymentStatus.COMPLETED) {
      payment.status = PaymentStatus.REFUNDED;
      await this.paymentRepository.save(payment);
    }
  }

  async onStockReserved(payload: BillingSagaPayload) {
    try {
      const payment = await this.processSagaPayment(payload);

      await this.emit(this.shippingQueue, 'billing.payment_confirmed', {
        orderId: payload.orderId,
        customerId: payload.customerId,
        productId: payload.productId,
        quantity: payload.quantity,
        amount: payload.amount,
      });

      return payment;
    } catch (error) {
      await Promise.all([
        this.emit(this.warehouseQueue, 'billing.payment_failed', {
          orderId: payload.orderId,
          customerId: payload.customerId,
          productId: payload.productId,
          quantity: payload.quantity,
          amount: payload.amount,
          reason: String(error),
        }),
        this.emit(this.orderQueue, 'billing.payment_failed', {
          orderId: payload.orderId,
          customerId: payload.customerId,
          productId: payload.productId,
          quantity: payload.quantity,
          amount: payload.amount,
          reason: String(error),
        }),
      ]);

      throw error;
    }
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
