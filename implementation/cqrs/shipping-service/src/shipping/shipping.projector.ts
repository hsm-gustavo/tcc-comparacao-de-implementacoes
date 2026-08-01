import { Controller } from '@nestjs/common';
import { Ctx, EventPattern, Payload, RmqContext } from '@nestjs/microservices';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Shipment, ShippingStatus } from './entities/shipment.entity';
import { ShippingEvent } from './entities/shipping-event.entity';

@Controller()
export class ShippingProjector {
  constructor(
    @InjectRepository(Shipment)
    private readonly shipmentRepository: Repository<Shipment>,
  ) {}

  @EventPattern('shipping.shipment_dispatched')
  async handleShipmentDispatched(
    @Payload() event: ShippingEvent,
    @Ctx() context: RmqContext,
  ) {
    try {
      let shipment = await this.shipmentRepository.findOneBy({
        orderId: event.aggregateId,
      });
      if (!shipment) {
        shipment = this.shipmentRepository.create({
          orderId: event.aggregateId,
        });
      }
      shipment.status = ShippingStatus.SHIPPED;
      shipment.trackingCode = event.data.trackingCode as string;

      await this.shipmentRepository.save(shipment);
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, true);
    }
  }
}
