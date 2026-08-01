import { Injectable } from '@nestjs/common';
import { Shipment, ShippingStatus } from './entities/shipment.entity';
import { EntityManager, Repository } from 'typeorm';
import { InjectRepository } from '@nestjs/typeorm';
import { CreateShipmentDto } from './dto/create-shipment.dto';
import { OutboxService } from 'src/outbox/outbox.service';

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
  private readonly orderQueue = 'order-saga-queue';
  private readonly billingQueue = 'billing-saga-queue';
  private readonly warehouseQueue = 'warehouse-saga-queue';

  constructor(
    @InjectRepository(Shipment)
    private shipmentRepository: Repository<Shipment>,
    private readonly outboxService: OutboxService,
  ) {}

  async createShipment(dto: CreateShipmentDto, manager?: EntityManager) {
    /* throw new InternalServerErrorException('Simulated shipping failure'); */
    const repo = manager
      ? manager.getRepository(Shipment)
      : this.shipmentRepository;

    const shipment = repo.create({
      orderId: dto.orderId,
      status: ShippingStatus.SHIPPED,
      trackingCode: `BR-${Math.random().toString(36).toUpperCase().substring(2, 9)}`,
    });

    return repo.save(shipment);
  }

  async createSagaShipment(
    payload: ShippingSagaPayload,
    manager: EntityManager,
  ) {
    const existingShipment = await manager.findOneBy(Shipment, {
      orderId: payload.orderId,
    });

    if (existingShipment) {
      return existingShipment;
    }

    return this.createShipment({ orderId: payload.orderId }, manager);
  }

  async getShipmentByOrderId(orderId: string) {
    return this.shipmentRepository.findOneBy({
      orderId: orderId,
    });
  }

  async cancelShipment(orderId: string, manager: EntityManager) {
    const shipment = await manager.findOneBy(Shipment, {
      orderId: orderId,
    });
    if (shipment) {
      shipment.status = ShippingStatus.CANCELLED;
      await manager.save(Shipment, shipment);
    }
  }

  async onPaymentConfirmed(payload: ShippingSagaPayload) {
    return this.shipmentRepository.manager.transaction(
      async (transactionalEntityManager) => {
        try {
          const shipment = await this.createSagaShipment(
            payload,
            transactionalEntityManager,
          );

          await this.outboxService.put(
            this.orderQueue,
            'shipping.shipment_created',
            {
              orderId: payload.orderId,
              customerId: payload.customerId,
              productId: payload.productId,
              quantity: payload.quantity,
              amount: payload.amount,
              trackingCode: shipment.trackingCode,
            },
            transactionalEntityManager,
          );

          return shipment;
        } catch (error) {
          await this.outboxService.put(
            this.orderQueue,
            'shipping.shipment_failed',
            {
              orderId: payload.orderId,
              customerId: payload.customerId,
              productId: payload.productId,
              quantity: payload.quantity,
              amount: payload.amount,
              reason: String(error),
            },
            transactionalEntityManager,
          );

          await this.outboxService.put(
            this.billingQueue,
            'shipping.shipment_failed',
            {
              orderId: payload.orderId,
              customerId: payload.customerId,
              productId: payload.productId,
              quantity: payload.quantity,
              amount: payload.amount,
              reason: String(error),
            },
            transactionalEntityManager,
          );

          await this.outboxService.put(
            this.warehouseQueue,
            'shipping.shipment_failed',
            {
              orderId: payload.orderId,
              customerId: payload.customerId,
              productId: payload.productId,
              quantity: payload.quantity,
              amount: payload.amount,
              reason: String(error),
            },
            transactionalEntityManager,
          );

          throw error;
        }
      },
    );
  }
}
