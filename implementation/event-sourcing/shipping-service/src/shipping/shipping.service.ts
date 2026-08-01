import { Injectable, NotFoundException } from '@nestjs/common';
import { Repository } from 'typeorm';
import { InjectRepository } from '@nestjs/typeorm';
import {
  ClientProxy,
  ClientProxyFactory,
  Transport,
} from '@nestjs/microservices';
import { ShippingEvent } from './entities/shipping-event.entity';
import { Shipment, ShippingStatus } from './entities/shipment.entity';

@Injectable()
export class ShippingService {
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';
  private readonly orderQueue = 'order-service-queue';

  constructor(
    @InjectRepository(ShippingEvent)
    private readonly eventRepository: Repository<ShippingEvent>,
    @InjectRepository(Shipment)
    private readonly shipmentRepository: Repository<Shipment>,
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

  async createShipmentFromEvent(orderId: string) {
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
    await this.eventRepository.save(dispatchEvent);

    await this.emit(this.orderQueue, 'shipping.shipment_created', {
      orderId,
      trackingCode,
    });

    return { orderId, trackingCode, status: 'SHIPPED' };
  }

  async getShipmentByOrderId(orderId: string) {
    const state = await this.rebuildShippingState(orderId);
    if (!state.exists) throw new NotFoundException('Shipment record not found');
    return state;
  }

  private async rebuildShippingState(orderId: string) {
    const events = await this.eventRepository.find({
      where: { aggregateId: orderId },
      order: { createdAt: 'ASC' },
    });

    if (events.length === 0) {
      return { exists: false, orderId, trackingCode: null, status: 'PENDING' }; // Corresponde ao ShippingStatus.PENDING
    }

    let trackingCode: string | null = null;
    let status = 'PENDING';

    for (const event of events) {
      switch (event.type) {
        case 'ShipmentRequested':
          status = 'PENDING'; // ShippingStatus.PENDING
          break;
        case 'ShipmentDispatched':
          status = 'SHIPPED'; // ShippingStatus.SHIPPED
          trackingCode = event.data.trackingCode || null;
          break;
        case 'ShipmentCancelled':
          status = 'CANCELLED'; // ShippingStatus.CANCELLED
          break;
      }
    }

    return {
      exists: true,
      orderId,
      trackingCode,
      status,
    };
  }

  async replayEvents() {
    const events = await this.eventRepository.find({
      order: { createdAt: 'ASC' },
    });

    let processedCount = 0;

    for (const event of events) {
      const orderId = event.aggregateId;

      if (event.type === 'ShipmentRequested') {
        let shipment = await this.shipmentRepository.findOneBy({ orderId });

        if (!shipment) {
          shipment = this.shipmentRepository.create({
            orderId: orderId,
            status: ShippingStatus.PENDING,
          });
          await this.shipmentRepository.save(shipment);
          processedCount++;
        }
      }

      if (event.type === 'ShipmentDispatched') {
        const shipment = await this.shipmentRepository.findOneBy({ orderId });
        if (shipment) {
          shipment.status = ShippingStatus.SHIPPED;
          shipment.trackingCode =
            event.data.trackingCode || shipment.trackingCode;
          await this.shipmentRepository.save(shipment);
          processedCount++;
        }
      }

      if (event.type === 'ShipmentCancelled') {
        const shipment = await this.shipmentRepository.findOneBy({ orderId });
        if (shipment) {
          shipment.status = ShippingStatus.CANCELLED;
          await this.shipmentRepository.save(shipment);
          processedCount++;
        }
      }
    }

    return {
      success: true,
      message: `Replay finalizado. ${processedCount} eventos de envios processados.`,
    };
  }
}
