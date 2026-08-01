import { Injectable } from '@nestjs/common';
import { Repository } from 'typeorm';
import { InjectRepository } from '@nestjs/typeorm';
import { CreateOrderDto } from './dto/create-order.dto';
import { OrderEvent } from './entities/order-event.entity';
import {
  ClientProxy,
  ClientProxyFactory,
  Transport,
} from '@nestjs/microservices';
import { OrderItem } from './entities/order-item.entity';
import { Order, OrderStatus } from './entities/order.entity';

@Injectable()
export class OrderService {
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';

  private readonly TargetQueue = 'warehouse-inventory-queue';

  constructor(
    @InjectRepository(OrderEvent)
    private orderEventRepo: Repository<OrderEvent>,
    @InjectRepository(Order) private readonly orderRepo: Repository<Order>,
    @InjectRepository(OrderItem)
    private readonly orderItemRepo: Repository<OrderItem>,
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
    payload: Record<string, unknown>,
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
    const orderId = crypto.randomUUID();

    const event = this.orderEventRepo.create({
      aggregateId: orderId,
      type: 'OrderPlaced',
      data: {
        customerId: dto.customerId,
        productId: dto.productId,
        quantity: dto.quantity,
        amount: dto.amount,
      },
    });

    await this.orderEventRepo.save(event);

    await this.emit(this.TargetQueue, 'order.placed', {
      orderId,
      ...event.data,
    });

    return { orderId, status: 'RECEIVED' };
  }

  async linkShipment(payload: { orderId: string; trackingCode: string }) {
    const event = this.orderEventRepo.create({
      aggregateId: payload.orderId,
      type: 'OrderShipmentLinked',
      data: {
        trackingCode: payload.trackingCode,
      },
    });

    return this.orderEventRepo.save(event);
  }

  async replayEvents() {
    const events = await this.orderEventRepo.find({
      order: { createdAt: 'ASC' },
    });

    let procCount = 0;

    for (const event of events) {
      const orderId = event.aggregateId;

      if (event.type === 'OrderPlaced') {
        let order = await this.orderRepo.findOneBy({ id: orderId });

        if (!order) {
          order = this.orderRepo.create({
            id: orderId,
            customerId: event.data.customerId!,
            totalAmount: event.data.amount!,
            status: OrderStatus.PENDING,
          });
          await this.orderRepo.save(order);

          const orderItem = this.orderItemRepo.create({
            order,
            productId: event.data.productId!,
            quantity: event.data.quantity!,
          });
          await this.orderItemRepo.save(orderItem);
        } else {
          order.customerId = event.data.customerId!;
          order.totalAmount = event.data.amount!;
          await this.orderRepo.save(order);
        }
        procCount++;
      }

      if (event.type === 'OrderShipmentLinked') {
        const order = await this.orderRepo.findOneBy({ id: orderId });

        if (order) {
          order.status = OrderStatus.CONFIRMED;
          await this.orderRepo.save(order);
          procCount++;
        }
      }
    }

    return {
      success: true,
      message: `Replay finalizado. ${procCount} eventos de pedido processados.`,
    };
  }
}
