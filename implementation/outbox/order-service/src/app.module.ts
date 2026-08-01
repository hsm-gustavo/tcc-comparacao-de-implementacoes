import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Order } from './order/entities/order.entity';
import { OrderItem } from './order/entities/order-item.entity';
import { OrderModule } from './order/order.module';
import { ScheduleModule } from '@nestjs/schedule';
import { Outbox } from './outbox/entities/outbox.entity';

@Module({
  imports: [
    TypeOrmModule.forRoot({
      type: 'postgres',
      host: 'localhost',
      port: 5434,
      username: 'user',
      password: 'password',
      database: 'order',
      entities: [Order, OrderItem, Outbox],
      synchronize: true,
    }),
    OrderModule,
    ScheduleModule.forRoot(),
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
