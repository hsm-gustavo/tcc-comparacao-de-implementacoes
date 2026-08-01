import { Controller } from '@nestjs/common';
import { EventPattern, Payload, Ctx, RmqContext } from '@nestjs/microservices';
import { BillingCommandService } from './billing.command.service';

@Controller()
export class BillingListener {
  constructor(private readonly commandService: BillingCommandService) {}

  @EventPattern('warehouse.stock_reserved')
  async handleStockReserved(
    @Payload()
    payload: { orderId: string; totalAmount: number } & Record<string, unknown>,
    @Ctx() context: RmqContext,
  ) {
    try {
      await this.commandService.processPayment(
        payload.orderId,
        payload.totalAmount,
      );
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      await this.commandService.processPaymentFailed(
        payload.orderId,
        payload.totalAmount,
      );
      context.getChannelRef().ack(context.getMessage());
    }
  }
}
