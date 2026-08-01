import { Injectable } from '@nestjs/common';
import { Repository } from 'typeorm';
import { Order, OrderStatus } from './entities/order.entity';
import { OrderItem } from './entities/order-item.entity';
import { InjectRepository } from '@nestjs/typeorm';
import { CreateOrderDto } from './dto/create-order.dto';
import { OutboxService } from '../outbox/outbox.service';

type OrderSagaPayload = {
  orderId: string;
  customerId: string;
  productId: string;
  quantity: number;
  amount: number;
  trackingCode?: string;
  reason?: string;
};

@Injectable()
export class OrderService {
  private readonly warehouseQueue = 'warehouse-saga-queue';

  constructor(
    @InjectRepository(Order) private orderRepo: Repository<Order>,
    private readonly outboxService: OutboxService,
  ) {}

  async createOrder(dto: CreateOrderDto) {
    return this.orderRepo.manager.transaction(
      async (transactionalEntityManager) => {
        const order = transactionalEntityManager.create(Order, {
          customerId: dto.customerId,
          totalAmount: dto.amount,
        });
        const savedOrder = await transactionalEntityManager.save(Order, order);

        const item = transactionalEntityManager.create(OrderItem, {
          order: savedOrder,
          productId: dto.productId,
          quantity: dto.quantity,
        });
        await transactionalEntityManager.save(OrderItem, item);

        await this.outboxService.put(
          this.warehouseQueue,
          'order.created',
          {
            orderId: savedOrder.id,
            customerId: savedOrder.customerId,
            productId: item.productId,
            quantity: item.quantity,
            amount: savedOrder.totalAmount,
          } as OrderSagaPayload,
          transactionalEntityManager,
        );
      },
    );
  }

  async markConfirmed(payload: OrderSagaPayload) {
    const order = await this.orderRepo.findOneBy({ id: payload.orderId });

    if (!order || order.status === OrderStatus.CANCELLED) {
      return order;
    }

    order.status = OrderStatus.CONFIRMED;
    return this.orderRepo.save(order);
  }

  async markCancelled(payload: OrderSagaPayload) {
    const order = await this.orderRepo.findOneBy({ id: payload.orderId });

    if (!order || order.status === OrderStatus.CONFIRMED) {
      return order;
    }

    order.status = OrderStatus.CANCELLED;
    return this.orderRepo.save(order);
  }
}
