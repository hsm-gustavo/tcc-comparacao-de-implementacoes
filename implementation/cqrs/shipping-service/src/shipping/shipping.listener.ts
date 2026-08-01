import { Controller } from '@nestjs/common';
import { Ctx, EventPattern, Payload, RmqContext } from '@nestjs/microservices';
import { ShippingCommandService } from './shipping.command.service';

@Controller()
export class ShippingListener {
  constructor(private readonly commandService: ShippingCommandService) {}

  @EventPattern('billing.payment_completed')
  async handlePaymentCompleted(
    @Payload() payload: { orderId: string } & Record<string, unknown>,
    @Ctx() context: RmqContext,
  ) {
    try {
      await this.commandService.generateShipment(payload.orderId);
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, false);
    }
  }
}
