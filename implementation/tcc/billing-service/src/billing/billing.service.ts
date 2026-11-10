import { BadRequestException, Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Payment, PaymentStatus } from './entities/payment.entity';
import { EntityManager, Repository } from 'typeorm';
import { ProcessPaymentDto } from './dto/process-payment.dto';

@Injectable()
export class BillingService {
  constructor(
    @InjectRepository(Payment) private paymentRepository: Repository<Payment>,
  ) {}

  private withOrder<T>(
    orderId: string,
    work: (manager: EntityManager) => Promise<T>,
  ): Promise<T> {
    return this.paymentRepository.manager.transaction(async (manager) => {
      await manager.query(
        'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
        [orderId],
      );
      return work(manager);
    });
  }

  async preparePayment(dto: ProcessPaymentDto) {
    return this.withOrder(dto.orderId, async (manager) => {
      const existing = await manager.findOneBy(Payment, {
        orderId: dto.orderId,
      });
      if (existing) {
        if (
          [PaymentStatus.FAILED, PaymentStatus.REFUNDED].includes(
            existing.status,
          )
        )
          throw new BadRequestException('Payment cancelled');
        return existing;
      }
      return manager.save(
        Payment,
        manager.create(Payment, {
          orderId: dto.orderId,
          amount: dto.amount,
          status: PaymentStatus.PENDING,
        }),
      );
    });
  }

  async commitPayment(orderId: string) {
    return this.withOrder(orderId, async (manager) => {
      const payment = await manager.findOneBy(Payment, { orderId });
      if (!payment) throw new BadRequestException('Payment not found');
      if (payment.status === PaymentStatus.COMPLETED) return payment;
      if (payment.status !== PaymentStatus.PENDING)
        throw new BadRequestException('Payment cancelled');
      payment.status = PaymentStatus.COMPLETED;
      return manager.save(Payment, payment);
    });
  }

  async rollbackPayment(orderId: string) {
    return this.withOrder(orderId, async (manager) => {
      const payment = await manager.findOneBy(Payment, { orderId });
      if (!payment) {
        // Empty cancel must prevent a queued, late Prepare from recreating the effect.
        return manager.save(
          Payment,
          manager.create(Payment, {
            orderId,
            amount: 0,
            status: PaymentStatus.FAILED,
          }),
        );
      }
      if (
        [PaymentStatus.COMPLETED, PaymentStatus.REFUNDED].includes(
          payment.status,
        )
      )
        throw new BadRequestException('Payment already committed');
      if (payment.status === PaymentStatus.FAILED) return payment;
      payment.status = PaymentStatus.FAILED;
      return manager.save(Payment, payment);
    });
  }

  async processPayment(dto: ProcessPaymentDto) {
    await this.preparePayment(dto);
    return this.commitPayment(dto.orderId);
  }

  async refundPayment(orderId: string) {
    return this.withOrder(orderId, async (manager) => {
      const payment = await manager.findOneBy(Payment, { orderId });
      if (payment && payment.status === PaymentStatus.COMPLETED) {
        payment.status = PaymentStatus.REFUNDED;
        await manager.save(Payment, payment);
      }
    });
  }

  async findOne(orderId: string) {
    return this.paymentRepository.findOneBy({ orderId });
  }
}
