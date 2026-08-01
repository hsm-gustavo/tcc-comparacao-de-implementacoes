import { Controller, Post, Body } from '@nestjs/common';
import { ApiBody, ApiOperation, ApiTags } from '@nestjs/swagger';
import { OrderService } from './order.service';
import { CreateOrderDto } from './dto/create-order.dto';
import { Ctx, EventPattern, Payload, RmqContext } from '@nestjs/microservices';

@ApiTags('orders')
@Controller('orders')
export class OrderController {
  constructor(private readonly orderService: OrderService) {}

  @Post()
  @ApiOperation({ summary: 'Cria um pedido' })
  @ApiBody({ type: CreateOrderDto })
  async placeOrder(@Body() body: CreateOrderDto) {
    return this.orderService.createOrder(body);
  }

  @EventPattern('shipping.shipment_created')
  async handleShipmentCreated(
    @Payload() payload: { orderId: string; trackingCode: string },
    @Ctx() context: RmqContext,
  ) {
    try {
      await this.orderService.linkShipment(payload);
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, false);
      throw error;
    }
  }

  @Post('replay')
  @ApiOperation({
    summary: 'Reprocessa todos os eventos para reconstruir o estado',
  })
  async replayEvents() {
    return this.orderService.replayEvents();
  }
}
