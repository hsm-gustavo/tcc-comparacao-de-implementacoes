// order.projector.ts
import { Controller } from '@nestjs/common';
import { Ctx, EventPattern, Payload, RmqContext } from '@nestjs/microservices';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Order, OrderStatus } from './entities/order.entity';
import { OrderItem } from './entities/order-item.entity';
import { OrderCommandService } from './order.command.service';
import { OrderEvent } from './entities/order-event.entity';

@Controller()
export class OrderProjector {
  constructor(
    @InjectRepository(Order) private readonly orderRepo: Repository<Order>,
    @InjectRepository(OrderItem)
    private readonly orderItemRepo: Repository<OrderItem>,
    private readonly commandService: OrderCommandService,
  ) {}

  @EventPattern('order.placed')
  async handleOrderPlaced(
    @Payload()
    payload: OrderEvent,
    @Ctx() context: RmqContext,
  ) {
    try {
      const orderId = payload.aggregateId;
      const data = payload.data;

      let order = await this.orderRepo.findOneBy({ id: orderId });

      if (!order) {
        order = this.orderRepo.create({
          id: orderId,
          customerId: data.customerId,
          totalAmount: data.amount,
          status: OrderStatus.PENDING,
        });
        await this.orderRepo.save(order);

        const item = this.orderItemRepo.create({
          order: order,
          productId: data.productId,
          quantity: data.quantity,
        });
        await this.orderItemRepo.save(item);
      }
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, false);
      throw error;
    }
  }

  @EventPattern('shipping.shipment_created')
  async handleShipmentCreated(
    @Payload()
    data: {
      id: string;
      aggregateId: string;
      type: string;
      data: {
        trackingCode?: string;
        reason?: string;
      };
      createdAt: Date;
    },
    @Ctx() context: RmqContext,
  ) {
    try {
      const orderId = data.aggregateId;
      const trackingCode = data.data?.trackingCode;

      if (!orderId) {
        throw new Error(
          'O aggregateId (orderId) veio nulo ou indefinido no evento de envio.',
        );
      }

      await this.commandService.linkShipment({
        orderId: orderId,
        trackingCode: trackingCode as string,
      });

      const order = await this.orderRepo.findOneBy({ id: orderId });
      if (order) {
        order.status = OrderStatus.CONFIRMED;
        await this.orderRepo.save(order);
      }

      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, false);
      throw error;
    }
  }
}
