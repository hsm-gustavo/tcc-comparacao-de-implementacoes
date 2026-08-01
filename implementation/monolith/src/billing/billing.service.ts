import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Payment, PaymentStatus } from './entities/payment.entity';
import { EntityManager, Repository } from 'typeorm';
import { Order } from 'src/order/entities/order.entity';

@Injectable()
export class BillingService {
  constructor(
    @InjectRepository(Payment) private paymentRepository: Repository<Payment>,
  ) {}

  async processPayment(order: Order, amount: number, manager?: EntityManager) {
    const repo = manager
      ? manager.getRepository(Payment)
      : this.paymentRepository;

    const payment = repo.create({
      order: order,
      amount: amount,
      status: PaymentStatus.PENDING,
    });

    await repo.save(payment);

    /* 20% chance of failure for later testing
    const isSuccessful = Math.random() < 0.2;

    if (!isSuccessful) {
      payment.status = PaymentStatus.FAILED;
      await repo.save(payment);
      throw new BadRequestException('Payment declined by the bank');
    } */

    payment.status = PaymentStatus.COMPLETED;
    return repo.save(payment);
  }

  async refundPayment(orderId: string) {
    const payment = await this.paymentRepository.findOneBy({
      order: { id: orderId },
    });
    if (payment && payment.status === PaymentStatus.COMPLETED) {
      payment.status = PaymentStatus.REFUNDED;
      await this.paymentRepository.save(payment);
    }
  }

  async findOne(orderId: string) {
    return this.paymentRepository.findOneBy({
      order: { id: orderId },
    });
  }
}
