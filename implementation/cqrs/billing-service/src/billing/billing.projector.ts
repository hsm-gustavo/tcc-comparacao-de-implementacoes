import { Controller } from '@nestjs/common';
import { Ctx, EventPattern, Payload, RmqContext } from '@nestjs/microservices';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Payment, PaymentStatus } from './entities/payment.entity';

@Controller()
export class BillingProjector {
  constructor(
    @InjectRepository(Payment)
    private readonly paymentRepository: Repository<Payment>,
  ) {}

  @EventPattern('billing.payment_completed')
  async handlePaymentCompleted(
    @Payload()
    event: {
      orderId: string;
      amount: number;
    },
    @Ctx() context: RmqContext,
  ) {
    try {
      await this.updatePaymentStatus(
        event.orderId,
        PaymentStatus.COMPLETED,
        event.amount,
      );
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, true);
      throw error;
    }
  }

  @EventPattern('billing.payment_failed')
  async handlePaymentFailed(
    @Payload() event: { orderId: string; amount: number; reason: string },
    @Ctx() context: RmqContext,
  ) {
    try {
      await this.updatePaymentStatus(
        event.orderId,
        PaymentStatus.FAILED,
        event.amount,
      );
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, true);
      throw error;
    }
  }

  private async updatePaymentStatus(
    orderId: string,
    status: PaymentStatus,
    amount: number,
  ) {
    let payment = await this.paymentRepository.findOneBy({ orderId });
    if (!payment) {
      payment = this.paymentRepository.create({ orderId, amount, status });
    }
    payment.status = status;
    await this.paymentRepository.save(payment);
  }
}
