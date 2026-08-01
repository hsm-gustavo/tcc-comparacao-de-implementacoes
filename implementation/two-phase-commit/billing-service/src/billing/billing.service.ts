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

  async preparePayment(dto: ProcessPaymentDto) {
    const existingPayment = await this.paymentRepository.findOneBy({
      orderId: dto.orderId,
    });

    if (existingPayment) {
      return existingPayment;
    }

    const payment = this.paymentRepository.create({
      orderId: dto.orderId,
      amount: dto.amount,
      status: PaymentStatus.PENDING,
    });

    return this.paymentRepository.save(payment);
  }

  async commitPayment(orderId: string) {
    const payment = await this.paymentRepository.findOneBy({
      orderId,
    });

    if (!payment) {
      throw new BadRequestException('Payment not found');
    }

    if (payment.status === PaymentStatus.COMPLETED) {
      return payment;
    }

    payment.status = PaymentStatus.COMPLETED;
    return this.paymentRepository.save(payment);
  }

  async rollbackPayment(orderId: string) {
    const payment = await this.paymentRepository.findOneBy({
      orderId,
    });

    if (!payment) {
      return null;
    }

    if (payment.status === PaymentStatus.COMPLETED) {
      return payment;
    }

    payment.status = PaymentStatus.FAILED;
    return this.paymentRepository.save(payment);
  }

  async processPayment(dto: ProcessPaymentDto) {
    await this.preparePayment(dto);

    /* //20% chance of failure for later testing
    const isSuccessful = Math.random() < 0.2;

    if (!isSuccessful) {
      await this.rollbackPayment(dto.orderId);
      throw new BadRequestException('Payment declined by the bank');
    } */

    return this.commitPayment(dto.orderId);
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
