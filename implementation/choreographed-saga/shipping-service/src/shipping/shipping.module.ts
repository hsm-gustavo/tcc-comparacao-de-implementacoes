import { MessageRecovery } from '../message-recovery';
import { Module } from '@nestjs/common';
import { ShippingService } from './shipping.service';
import { ShippingController } from './shipping.controller';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Shipment } from './entities/shipment.entity';

@Module({
  imports: [TypeOrmModule.forFeature([Shipment])],
  controllers: [ShippingController],
  providers: [ShippingService, MessageRecovery],
  exports: [ShippingService],
})
export class ShippingModule {}
