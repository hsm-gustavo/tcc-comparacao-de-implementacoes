import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Order } from './order/entities/order.entity';
import { OrderItem } from './order/entities/order-item.entity';
import { OrderModule } from './order/order.module';

@Module({
  imports: [
    TypeOrmModule.forRoot({
      type: 'postgres',
      host: process.env.POSTGRES_HOST ?? 'localhost',
      port: Number(process.env.POSTGRES_PORT ?? 5434),
      username: 'user',
      password: 'password',
      database: 'order',
      entities: [Order, OrderItem],
      synchronize: true,
      extra: {
        max: Number(process.env.DB_POOL_MAX ?? 80),
        connectionTimeoutMillis: 2000,
        idleTimeoutMillis: 30000,
      },
    }),
    TypeOrmModule.forFeature([Order, OrderItem]),
    OrderModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
