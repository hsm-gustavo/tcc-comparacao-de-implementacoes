import { BadRequestException, Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Payment, PaymentStatus } from './entities/payment.entity';
import { Repository } from 'typeorm';
import { ProcessPaymentDto } from './dto/process-payment.dto';

@Injectable()
export class BillingService {
  constructor(
    @InjectRepository(Payment) private paymentRepository: Repository<Payment>,
  ) {}

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

  async preparePayment(dto: ProcessPaymentDto) {
    const existing = await this.paymentRepository.findOneBy({
      orderId: dto.orderId,
    });

    if (existing) return existing;

    const payment = this.paymentRepository.create({
      orderId: dto.orderId,
      amount: dto.amount,
      status: PaymentStatus.PENDING,
    });

    return this.paymentRepository.save(payment);
  }

  async commitPayment(orderId: string) {
    const payment = await this.paymentRepository.findOneBy({ orderId });
    if (!payment) throw new BadRequestException('Payment not found');

    if (payment.status === PaymentStatus.COMPLETED) return payment;

    payment.status = PaymentStatus.COMPLETED;
    return this.paymentRepository.save(payment);
  }

  async rollbackPayment(orderId: string) {
    const payment = await this.paymentRepository.findOneBy({ orderId });
    if (!payment) return null;

    payment.status = PaymentStatus.FAILED;
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

  async findOne(orderId: string) {
    return this.paymentRepository.findOneBy({
      orderId: orderId,
    });
  }
}
