import { Controller, Get, Param, Post } from '@nestjs/common';
import { ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { ShippingService } from './shipping.service';
import { Ctx, EventPattern, Payload, RmqContext } from '@nestjs/microservices';

@ApiTags('shipping')
@Controller('shipping')
export class ShippingController {
  constructor(private readonly shippingService: ShippingService) {}

  @Get('tracking/:orderId')
  @ApiOperation({
    summary: 'Consulta o estado do envio e tracking reconstruindo o histórico',
  })
  @ApiParam({ name: 'orderId', type: String })
  async getTracking(@Param('orderId') orderId: string) {
    return this.shippingService.getShipmentByOrderId(orderId);
  }

  @EventPattern('billing.payment_completed')
  async handlePaymentCompleted(
    @Payload() payload: { orderId: string; amount: number },
    @Ctx() context: RmqContext,
  ) {
    try {
      await this.shippingService.createShipmentFromEvent(payload.orderId);

      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, false);
      throw error;
    }
  }

  @Post('replay')
  @ApiOperation({
    summary:
      'Executa o replay dos eventos de logística aplicando Upsert na tabela de shipments',
  })
  async triggerReplay() {
    return this.shippingService.replayEvents();
  }
}
