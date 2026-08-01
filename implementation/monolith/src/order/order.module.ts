import { Module } from '@nestjs/common';
import { OrderService } from './order.service';
import { OrderController } from './order.controller';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Order } from './entities/order.entity';
import { WarehouseModule } from 'src/warehouse/warehouse.module';
import { BillingModule } from 'src/billing/billing.module';
import { ShippingModule } from 'src/shipping/shipping.module';
import { OrderItem } from './entities/order-item.entity';

@Module({
  imports: [
    TypeOrmModule.forFeature([Order, OrderItem]),
    WarehouseModule,
    BillingModule,
    ShippingModule,
  ],
  controllers: [OrderController],
  providers: [OrderService],
})
export class OrderModule {}
