import { Module } from '@nestjs/common';
import { OrderService } from './order.service';
import { OrderController } from './order.controller';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Order } from './entities/order.entity';
import { OrderItem } from './entities/order-item.entity';
import { HttpModule } from '@nestjs/axios';
import { OrderEvent } from './entities/order-event.entity';

@Module({
  imports: [
    TypeOrmModule.forFeature([Order, OrderItem, OrderEvent]),
    HttpModule.register({
      timeout: 5000,
      maxRedirects: 5,
    }),
  ],
  controllers: [OrderController],
  providers: [OrderService],
})
export class OrderModule {}
