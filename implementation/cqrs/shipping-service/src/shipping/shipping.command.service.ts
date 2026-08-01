import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { ShippingEvent } from './entities/shipping-event.entity';
import {
  ClientProxy,
  ClientProxyFactory,
  Transport,
} from '@nestjs/microservices';

@Injectable()
export class ShippingCommandService {
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';
  private readonly shippingProjectionQueue = 'shipping-projection-queue';
  private readonly orderQueue = 'order-service-queue';

  constructor(
    @InjectRepository(ShippingEvent)
    private readonly eventRepository: Repository<ShippingEvent>,
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

  private async emit(queue: string, pattern: string, payload: ShippingEvent) {
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

  async generateShipment(orderId: string) {
    const requestEvent = this.eventRepository.create({
      aggregateId: orderId,
      type: 'ShipmentRequested',
      data: {},
    });
    await this.eventRepository.save(requestEvent);

    const trackingCode = `BR-${Math.random().toString(36).toUpperCase().substring(2, 9)}`;

    const dispatchEvent = this.eventRepository.create({
      aggregateId: orderId,
      type: 'ShipmentDispatched',
      data: { trackingCode },
    });
    const savedEvent = await this.eventRepository.save(dispatchEvent);

    await this.emit(
      this.shippingProjectionQueue,
      'shipping.shipment_dispatched',
      savedEvent,
    );

    await this.emit(this.orderQueue, 'shipping.shipment_created', savedEvent);

    return { orderId, trackingCode, status: 'SHIPPED' };
  }
}
