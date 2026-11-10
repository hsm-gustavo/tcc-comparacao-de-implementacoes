import { MessageRecovery } from '../message-recovery';
import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { ShippingService } from './shipping.service';
import { CreateShipmentDto } from './dto/create-shipment.dto';
import { Ctx, EventPattern, Payload, RmqContext } from '@nestjs/microservices';

@ApiTags('shipping')
@Controller('shipping')
export class ShippingController {
  constructor(
    private readonly shippingService: ShippingService,
    private readonly recovery: MessageRecovery,
  ) {}

  @Post()
  @ApiOperation({ summary: 'Internal API: Create shipment' })
  create(@Body() dto: CreateShipmentDto) {
    return this.shippingService.createShipment(dto);
  }

  @Get('tracking/:orderId')
  @ApiOperation({ summary: 'Consulta tracking por orderId' })
  @ApiParam({ name: 'orderId', type: String })
  getTracking(@Param('orderId') orderId: string) {
    return this.shippingService.getShipmentByOrderId(orderId);
  }

  @EventPattern('billing.payment_confirmed')
  async handlePaymentConfirmed(
    @Payload() payload: Record<string, unknown>,
    @Ctx() context: RmqContext,
  ) {
    try {
      const result = await this.shippingService.onPaymentConfirmed(
        payload as any,
      );
      context.getChannelRef().ack(context.getMessage());
      return result;
    } catch (error) {
      await this.recovery.handle(context, error, 'shipping-saga-queue');
    }
  }
}
