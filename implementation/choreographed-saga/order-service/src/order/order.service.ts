import { Injectable, InternalServerErrorException } from '@nestjs/common';
import { Repository } from 'typeorm';
import { Order, OrderStatus } from './entities/order.entity';
import { OrderItem } from './entities/order-item.entity';
import { InjectRepository } from '@nestjs/typeorm';
import { CreateOrderDto } from './dto/create-order.dto';
import {
  ClientProxy,
  ClientProxyFactory,
  Transport,
} from '@nestjs/microservices';

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
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';
  private readonly warehouseQueue = 'warehouse-saga-queue';

  constructor(
    @InjectRepository(Order) private orderRepo: Repository<Order>,
    @InjectRepository(OrderItem) private orderItemRepo: Repository<OrderItem>,
  ) {}

  private createClient(queue: string): ClientProxy {
    return ClientProxyFactory.create({
      transport: Transport.RMQ,
      options: {
        urls: [this.rabbitUrl],
        queue,
        queueOptions: {
          durable: true,
        },
      },
    });
  }

  private async emit(
    queue: string,
    pattern: string,
    payload: OrderSagaPayload,
  ) {
    const client = this.createClient(queue);

    try {
      await new Promise<void>((resolve, reject) => {
        client.emit(pattern, payload).subscribe({
          complete: resolve,
          error: reject,
        });
      });
    } finally {
      await client.close();
    }
  }

  async createOrder(dto: CreateOrderDto) {
    const order = this.orderRepo.create({
      customerId: dto.customerId,
      totalAmount: dto.amount,
    });
    const savedOrder = await this.orderRepo.save(order);

    const item = this.orderItemRepo.create({
      order: savedOrder,
      productId: dto.productId,
      quantity: dto.quantity,
    });
    await this.orderItemRepo.save(item);

    try {
      await this.emit(this.warehouseQueue, 'order.created', {
        orderId: savedOrder.id,
        customerId: dto.customerId,
        productId: dto.productId,
        quantity: dto.quantity,
        amount: dto.amount,
      });

      return savedOrder;
    } catch (err) {
      savedOrder.status = OrderStatus.CANCELLED;
      await this.orderRepo.save(savedOrder);

      throw new InternalServerErrorException(
        `Distributed Order failed: ${String(err)}`,
      );
    }
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
