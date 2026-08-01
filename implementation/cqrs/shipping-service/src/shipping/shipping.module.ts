import { Module } from '@nestjs/common';
import { ShippingController } from './shipping.controller';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Shipment } from './entities/shipment.entity';
import { ShippingEvent } from './entities/shipping-event.entity';
import { ShippingProjector } from './shipping.projector';
import { ShippingCommandService } from './shipping.command.service';
import { ShippingQueryService } from './shipping.query.service';
import { ShippingListener } from './shipping.listener';

@Module({
  imports: [TypeOrmModule.forFeature([Shipment, ShippingEvent])],
  controllers: [ShippingController, ShippingProjector, ShippingListener],
  providers: [ShippingCommandService, ShippingQueryService],
})
export class ShippingModule {}
