import { Injectable } from '@nestjs/common';
import { Shipment, ShippingStatus } from './entities/shipment.entity';
import { Repository } from 'typeorm';
import { InjectRepository } from '@nestjs/typeorm';
import { CreateShipmentDto } from './dto/create-shipment.dto';
import {
  ClientProxy,
  ClientProxyFactory,
  Transport,
} from '@nestjs/microservices';

type ShippingSagaPayload = {
  orderId: string;
  customerId?: string;
  productId?: string;
  quantity?: number;
  amount?: number;
  reason?: string;
  trackingCode?: string;
};

@Injectable()
export class ShippingService {
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';
  private readonly orderQueue = 'order-saga-queue';
  private readonly billingQueue = 'billing-saga-queue';
  private readonly warehouseQueue = 'warehouse-saga-queue';

  constructor(
    @InjectRepository(Shipment)
    private shipmentRepository: Repository<Shipment>,
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
    payload: ShippingSagaPayload,
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

  async createShipment(dto: CreateShipmentDto) {
    /* throw new InternalServerErrorException('Simulated shipping failure'); */

    const shipment = this.shipmentRepository.create({
      orderId: dto.orderId,
      status: ShippingStatus.SHIPPED,
      trackingCode: `BR-${Math.random().toString(36).toUpperCase().substring(2, 9)}`,
    });

    return this.shipmentRepository.save(shipment);
  }

  async createSagaShipment(payload: ShippingSagaPayload) {
    const existingShipment = await this.getShipmentByOrderId(payload.orderId);

    if (existingShipment) {
      return existingShipment;
    }

    return this.createShipment({ orderId: payload.orderId });
  }

  async getShipmentByOrderId(orderId: string) {
    return this.shipmentRepository.findOneBy({
      orderId: orderId,
    });
  }

  async cancelShipment(orderId: string) {
    const shipment = await this.getShipmentByOrderId(orderId);
    if (shipment) {
      shipment.status = ShippingStatus.CANCELLED;
      await this.shipmentRepository.save(shipment);
    }
  }

  async onPaymentConfirmed(payload: ShippingSagaPayload) {
    try {
      const shipment = await this.createSagaShipment(payload);

      await this.emit(this.orderQueue, 'shipping.shipment_created', {
        orderId: payload.orderId,
        customerId: payload.customerId,
        productId: payload.productId,
        quantity: payload.quantity,
        amount: payload.amount,
        trackingCode: shipment.trackingCode,
      });

      return shipment;
    } catch (error) {
      await Promise.all([
        this.emit(this.orderQueue, 'shipping.shipment_failed', {
          orderId: payload.orderId,
          customerId: payload.customerId,
          productId: payload.productId,
          quantity: payload.quantity,
          amount: payload.amount,
          reason: String(error),
        }),
        this.emit(this.billingQueue, 'shipping.shipment_failed', {
          orderId: payload.orderId,
          customerId: payload.customerId,
          productId: payload.productId,
          quantity: payload.quantity,
          amount: payload.amount,
          reason: String(error),
        }),
        this.emit(this.warehouseQueue, 'shipping.shipment_failed', {
          orderId: payload.orderId,
          customerId: payload.customerId,
          productId: payload.productId,
          quantity: payload.quantity,
          amount: payload.amount,
          reason: String(error),
        }),
      ]);

      throw error;
    }
  }
}
