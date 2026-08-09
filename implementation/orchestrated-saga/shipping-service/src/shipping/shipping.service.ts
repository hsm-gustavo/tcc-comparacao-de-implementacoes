import { Injectable } from '@nestjs/common';
import { Shipment, ShippingStatus } from './entities/shipment.entity';
import { Repository } from 'typeorm';
import { InjectRepository } from '@nestjs/typeorm';
import { CreateShipmentDto } from './dto/create-shipment.dto';

@Injectable()
export class ShippingService {
  constructor(
    @InjectRepository(Shipment)
    private shipmentRepository: Repository<Shipment>,
  ) {}

  async createShipment(dto: CreateShipmentDto) {
    /* throw new InternalServerErrorException('Simulated shipping failure'); */

    const shipment = this.shipmentRepository.create({
      orderId: dto.orderId,
      status: ShippingStatus.SHIPPED,
      trackingCode: `BR-${Math.random().toString(36).toUpperCase().substring(2, 9)}`,
    });

    return this.shipmentRepository.save(shipment);
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
}
