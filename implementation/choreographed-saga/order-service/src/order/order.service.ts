import {
  Injectable,
  InternalServerErrorException,
  Logger,
} from '@nestjs/common';
import { Repository } from 'typeorm';
import { Order, OrderStatus } from './entities/order.entity';
import { OrderItem } from './entities/order-item.entity';
import { InjectRepository } from '@nestjs/typeorm';
import { CreateOrderDto } from './dto/create-order.dto';
import { SagaPublisher } from '../saga-publisher';

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
export class OrderService extends SagaPublisher {
  private readonly logger = new Logger(OrderService.name);
  private readonly warehouseQueue = 'warehouse-saga-queue';

  constructor(@InjectRepository(Order) private orderRepo: Repository<Order>) {
    super();
  }

  async createOrder(dto: CreateOrderDto) {
    const savedOrder = await this.orderRepo.manager.transaction(
      async (manager) => {
        const order = manager.create(Order, {
          customerId: dto.customerId,
          totalAmount: dto.amount,
        });
        const saved = await manager.save(Order, order);
        const item = manager.create(OrderItem, {
          order: saved,
          productId: dto.productId,
          quantity: dto.quantity,
        });
        await manager.save(OrderItem, item);
        return saved;
      },
    );

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
      // A failed publisher confirmation does not prove that the broker received nothing.
      // Keep the durable pending state; a late success event may still confirm it.
      this.logger.error({
        event: 'publication.outcome-unknown',
        orderId: savedOrder.id,
        reason: String(err),
      });

      throw new InternalServerErrorException(
        `Distributed Order failed: ${String(err)}`,
      );
    }
  }

  async markConfirmed(payload: OrderSagaPayload) {
    await this.orderRepo.update(
      { id: payload.orderId, status: OrderStatus.PENDING },
      { status: OrderStatus.CONFIRMED },
    );
    return this.orderRepo.findOneBy({ id: payload.orderId });
  }

  async markCancelled(payload: OrderSagaPayload) {
    await this.orderRepo.update(
      { id: payload.orderId, status: OrderStatus.PENDING },
      { status: OrderStatus.CANCELLED },
    );
    return this.orderRepo.findOneBy({ id: payload.orderId });
  }
}
