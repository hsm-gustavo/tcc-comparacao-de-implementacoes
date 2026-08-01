import { Module } from '@nestjs/common';
import { BillingController } from './billing.controller';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Payment } from './entities/payment.entity';
import { BillingEvent } from './entities/billing-event.entity';
import { BillingProjector } from './billing.projector';
import { BillingQueryService } from './billing.query.service';
import { BillingCommandService } from './billing.command.service';
import { BillingListener } from './billing.listener';

@Module({
  imports: [TypeOrmModule.forFeature([Payment, BillingEvent])],
  controllers: [BillingController, BillingProjector, BillingListener],
  providers: [BillingCommandService, BillingQueryService],
})
export class BillingModule {}
