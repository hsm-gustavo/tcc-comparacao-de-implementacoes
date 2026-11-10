import { MessageRecovery } from '../message-recovery';
import { Controller, Post, Body } from '@nestjs/common';
import { ApiBody, ApiOperation, ApiTags } from '@nestjs/swagger';
import { OrderService } from './order.service';
import { CreateOrderDto } from './dto/create-order.dto';
import { Ctx, EventPattern, Payload, RmqContext } from '@nestjs/microservices';

@ApiTags('orders')
@Controller('orders')
export class OrderController {
  constructor(
    private readonly orderService: OrderService,
    private readonly recovery: MessageRecovery,
  ) {}

  @Post()
  @ApiOperation({ summary: 'Cria um pedido' })
  @ApiBody({ type: CreateOrderDto })
  async placeOrder(@Body() body: CreateOrderDto) {
    return this.orderService.createOrder(body);
  }

  @EventPattern('shipping.shipment_created')
  async handleShipmentCreated(
    @Payload() payload: Record<string, unknown>,
    @Ctx() context: RmqContext,
  ) {
    try {
      const result = await this.orderService.markConfirmed(payload as any);
      context.getChannelRef().ack(context.getMessage());
      return result;
    } catch (error) {
      await this.recovery.handle(context, error, 'order-saga-queue');
    }
  }

  @EventPattern('warehouse.stock_failed')
  async handleWarehouseStockFailed(
    @Payload() payload: Record<string, unknown>,
    @Ctx() context: RmqContext,
  ) {
    try {
      const result = await this.orderService.markCancelled(payload as any);
      context.getChannelRef().ack(context.getMessage());
      return result;
    } catch (error) {
      await this.recovery.handle(context, error, 'order-saga-queue');
    }
  }

  @EventPattern('billing.payment_failed')
  async handleBillingPaymentFailed(
    @Payload() payload: Record<string, unknown>,
    @Ctx() context: RmqContext,
  ) {
    try {
      const result = await this.orderService.markCancelled(payload as any);
      context.getChannelRef().ack(context.getMessage());
      return result;
    } catch (error) {
      await this.recovery.handle(context, error, 'order-saga-queue');
    }
  }

  @EventPattern('shipping.shipment_failed')
  async handleShippingShipmentFailed(
    @Payload() payload: Record<string, unknown>,
    @Ctx() context: RmqContext,
  ) {
    try {
      const result = await this.orderService.markCancelled(payload as any);
      context.getChannelRef().ack(context.getMessage());
      return result;
    } catch (error) {
      await this.recovery.handle(context, error, 'order-saga-queue');
    }
  }
}
