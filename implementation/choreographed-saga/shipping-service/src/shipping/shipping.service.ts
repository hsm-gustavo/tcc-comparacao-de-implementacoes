import { Injectable } from '@nestjs/common';
import { Shipment, ShippingStatus } from './entities/shipment.entity';
import { Repository } from 'typeorm';
import { InjectRepository } from '@nestjs/typeorm';
import { CreateShipmentDto } from './dto/create-shipment.dto';
import { SagaPublisher } from '../saga-publisher';

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
export class ShippingService extends SagaPublisher {
  private readonly orderQueue = 'order-saga-queue';

  constructor(
    @InjectRepository(Shipment)
    private shipmentRepository: Repository<Shipment>,
  ) {
    super();
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
    return this.shipmentRepository.manager.transaction(async (manager) => {
      await manager.query(
        'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
        [payload.orderId],
      );
      const existing = await manager.findOneBy(Shipment, {
        orderId: payload.orderId,
      });
      if (existing) return existing;
      const shipment = manager.create(Shipment, {
        orderId: payload.orderId,
        status: ShippingStatus.SHIPPED,
        trackingCode: `BR-${Math.random().toString(36).toUpperCase().substring(2, 9)}`,
      });
      return manager.save(Shipment, shipment);
    });
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
    const shipment = await this.createSagaShipment(payload);
    if (shipment.status === ShippingStatus.SHIPPED) {
      await this.emit(this.orderQueue, 'shipping.shipment_created', {
        ...payload,
        trackingCode: shipment.trackingCode,
      });
    }
    return shipment;
  }
}
