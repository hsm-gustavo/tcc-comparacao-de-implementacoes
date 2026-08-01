import { Controller } from '@nestjs/common';
import { EventPattern, Payload, Ctx, RmqContext } from '@nestjs/microservices';
import { WarehouseCommandService } from './warehouse.command.service';

@Controller()
export class WarehouseListener {
  constructor(private readonly commandService: WarehouseCommandService) {}

  @EventPattern('order.placed')
  async handleOrderPlaced(
    @Payload()
    payload: {
      orderId: string;
      productId: string;
      quantity: number;
      amount: number;
    },
    @Ctx() context: RmqContext,
  ) {
    try {
      await this.commandService.reserveStock(
        payload.productId,
        payload.quantity,
        payload.orderId,
      );
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().ack(context.getMessage());
    }
  }
}
