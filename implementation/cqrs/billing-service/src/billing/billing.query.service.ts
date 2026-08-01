import { Injectable, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Payment } from './entities/payment.entity';

@Injectable()
export class BillingQueryService {
  constructor(
    @InjectRepository(Payment)
    private readonly paymentRepository: Repository<Payment>,
  ) {}

  async getPaymentByOrderId(orderId: string) {
    const payment = await this.paymentRepository.findOneBy({ orderId });
    if (!payment) throw new NotFoundException('Billing record not found');
    return payment;
  }
}
