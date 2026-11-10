import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { Shipment, ShippingStatus } from './entities/shipment.entity';
import { EntityManager, Repository } from 'typeorm';
import { InjectRepository } from '@nestjs/typeorm';
import { CreateShipmentDto } from './dto/create-shipment.dto';

@Injectable()
export class ShippingService {
  constructor(
    @InjectRepository(Shipment)
    private shipmentRepository: Repository<Shipment>,
  ) {}

  private withOrder<T>(
    orderId: string,
    work: (manager: EntityManager) => Promise<T>,
  ): Promise<T> {
    return this.shipmentRepository.manager.transaction(async (manager) => {
      await manager.query(
        'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
        [orderId],
      );
      return work(manager);
    });
  }

  async prepareShipment(dto: CreateShipmentDto) {
    return this.withOrder(dto.orderId, async (manager) => {
      const existing = await manager.findOneBy(Shipment, {
        orderId: dto.orderId,
      });
      if (existing) {
        if (existing.status === ShippingStatus.CANCELLED)
          throw new BadRequestException('Shipment cancelled');
        return existing;
      }
      return manager.save(
        Shipment,
        manager.create(Shipment, {
          orderId: dto.orderId,
          status: ShippingStatus.PENDING,
        }),
      );
    });
  }

  async commitShipment(orderId: string) {
    return this.withOrder(orderId, async (manager) => {
      const shipment = await manager.findOneBy(Shipment, { orderId });
      if (!shipment) throw new NotFoundException('Shipment not found');
      if (
        [ShippingStatus.SHIPPED, ShippingStatus.DELIVERED].includes(
          shipment.status,
        )
      )
        return shipment;
      if (shipment.status !== ShippingStatus.PENDING)
        throw new BadRequestException('Shipment cancelled');
      shipment.status = ShippingStatus.SHIPPED;
      shipment.trackingCode = `BR-${Math.random().toString(36).toUpperCase().substring(2, 9)}`;
      return manager.save(Shipment, shipment);
    });
  }

  async rollbackShipment(orderId: string) {
    return this.withOrder(orderId, async (manager) => {
      const shipment = await manager.findOneBy(Shipment, { orderId });
      if (!shipment)
        return manager.save(
          Shipment,
          manager.create(Shipment, {
            orderId,
            status: ShippingStatus.CANCELLED,
          }),
        );
      if (
        [ShippingStatus.SHIPPED, ShippingStatus.DELIVERED].includes(
          shipment.status,
        )
      )
        throw new BadRequestException('Shipment already committed');
      if (shipment.status === ShippingStatus.CANCELLED) return shipment;
      shipment.status = ShippingStatus.CANCELLED;
      return manager.save(Shipment, shipment);
    });
  }

  async createShipment(dto: CreateShipmentDto) {
    await this.prepareShipment(dto);
    return this.commitShipment(dto.orderId);
  }

  async getShipmentByOrderId(orderId: string) {
    return this.shipmentRepository.findOneBy({ orderId });
  }

  async cancelShipment(orderId: string) {
    return this.withOrder(orderId, async (manager) => {
      const shipment = await manager.findOneBy(Shipment, { orderId });
      if (shipment) {
        shipment.status = ShippingStatus.CANCELLED;
        await manager.save(Shipment, shipment);
      }
    });
  }
}
