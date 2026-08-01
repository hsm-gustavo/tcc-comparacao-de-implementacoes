import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { WarehouseModule } from './warehouse/warehouse.module';
import { ShippingModule } from './shipping/shipping.module';
import { Payment } from './billing/entities/payment.entity';
import { Shipment } from './shipping/entities/shipment.entity';
import { Product } from './warehouse/entities/product.entity';
import { BillingModule } from './billing/billing.module';
import { OrderModule } from './order/order.module';
import { Order } from './order/entities/order.entity';
import { OrderItem } from './order/entities/order-item.entity';

@Module({
  imports: [
    TypeOrmModule.forRoot({
      type: 'postgres',
      host: 'localhost',
      port: 5432,
      username: 'user',
      password: 'password',
      database: 'db',
      entities: [Payment, Shipment, Product, Order, OrderItem],
      synchronize: true,
    }),
    TypeOrmModule.forFeature([Payment, Shipment, Product, Order, OrderItem]),
    WarehouseModule,
    ShippingModule,
    BillingModule,
    OrderModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
