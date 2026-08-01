import { BadRequestException, Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { WarehouseEvent } from './entities/warehouse-event.entity';
import {
  ClientProxy,
  ClientProxyFactory,
  Transport,
} from '@nestjs/microservices';

@Injectable()
export class WarehouseCommandService {
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';
  private readonly projectionQueue = 'warehouse-projection-queue';
  private readonly billingQueue = 'billing-service-queue';

  constructor(
    @InjectRepository(WarehouseEvent)
    private readonly eventRepository: Repository<WarehouseEvent>,
  ) {}

  private createClient(queue: string): ClientProxy {
    return ClientProxyFactory.create({
      transport: Transport.RMQ,
      options: {
        urls: [this.rabbitUrl],
        queue,
        queueOptions: { durable: true },
      },
    });
  }

  private async emit(
    queue: string,
    pattern: string,
    payload: WarehouseEvent | Record<string, unknown>,
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

  async createProduct(dto: {
    name: string;
    price: number;
    stockQuantity: number;
  }) {
    const productId = crypto.randomUUID();

    const event = this.eventRepository.create({
      aggregateId: productId,
      type: 'ProductCreated',
      data: { name: dto.name, price: dto.price, quantity: dto.stockQuantity },
    });
    const savedEvent = await this.eventRepository.save(event);

    await this.emit(
      this.projectionQueue,
      'warehouse.product_created',
      savedEvent,
    );

    return { id: productId };
  }

  async reserveStock(productId: string, quantity: number, orderId: string) {
    const currentStock = await this.getCurrentStockFromEvents(productId);

    if (currentStock < quantity) {
      const failedEvent = this.eventRepository.create({
        aggregateId: productId,
        type: 'StockDeductionFailed',
        data: { orderId, quantity, reason: 'Insufficient stock' },
      });
      await this.eventRepository.save(failedEvent);
      throw new BadRequestException('Insufficient stock');
    }

    const successEvent = this.eventRepository.create({
      aggregateId: productId,
      type: 'StockDecreased',
      data: { orderId, quantity },
    });
    const savedEvent = await this.eventRepository.save(successEvent);

    await this.emit(
      this.projectionQueue,
      'warehouse.stock_decreased',
      savedEvent,
    );

    if (orderId !== 'MANUAL_HTTP') {
      const events = await this.eventRepository.find({
        where: { aggregateId: productId },
        order: { createdAt: 'ASC' },
      });
      const productCreatedEvent = events.find(
        (e) => e.type === 'ProductCreated',
      );
      const price = productCreatedEvent?.data?.price || 0;
      const totalAmount = price * quantity;

      await this.emit(this.billingQueue, 'warehouse.stock_reserved', {
        orderId,
        productId,
        quantity,
        totalAmount,
      });
    }

    return { status: 'SUCCESS' };
  }

  private async getCurrentStockFromEvents(productId: string): Promise<number> {
    const events = await this.eventRepository.find({
      where: { aggregateId: productId },
    });
    let stock = 0;
    for (const event of events) {
      if (event.type === 'ProductCreated') stock += event.data.quantity || 0;
      if (event.type === 'StockDecreased') stock -= event.data.quantity || 0;
    }
    return stock;
  }
}
