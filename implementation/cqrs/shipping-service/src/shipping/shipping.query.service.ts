import { Injectable, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Shipment } from './entities/shipment.entity';

@Injectable()
export class ShippingQueryService {
  constructor(
    @InjectRepository(Shipment)
    private readonly shipmentRepository: Repository<Shipment>,
  ) {}

  async getShipmentByOrderId(orderId: string) {
    const shipment = await this.shipmentRepository.findOneBy({ orderId });
    if (!shipment) throw new NotFoundException('Shipment record not found');
    return shipment;
  }
}
