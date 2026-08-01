import { Injectable } from '@nestjs/common';
import { Shipment, ShippingStatus } from './entities/shipment.entity';
import { EntityManager, Repository } from 'typeorm';
import { InjectRepository } from '@nestjs/typeorm';
import { Order } from 'src/order/entities/order.entity';

@Injectable()
export class ShippingService {
  constructor(
    @InjectRepository(Shipment)
    private shipmentRepository: Repository<Shipment>,
  ) {}

  createShipment(order: Order, manager?: EntityManager) {
    /* throw new InternalServerErrorException('Simulated shipping failure'); */

    const repo = manager
      ? manager.getRepository(Shipment)
      : this.shipmentRepository;

    const shipment = repo.create({
      order: order,
      status: ShippingStatus.SHIPPED,
      trackingCode: `BR-${Math.random().toString(36).toUpperCase().substring(2, 9)}`,
    });

    return repo.save(shipment);
  }

  async getShipmentByOrderId(orderId: string) {
    return this.shipmentRepository.findOneBy({
      order: { id: orderId },
    });
  }

  async cancelShipment(orderId: string) {
    const shipment = await this.getShipmentByOrderId(orderId);
    if (shipment) {
      shipment.status = ShippingStatus.CANCELLED;
      await this.shipmentRepository.save(shipment);
    }
  }
}
