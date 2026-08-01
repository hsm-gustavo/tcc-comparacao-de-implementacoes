import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Product } from './warehouse/entities/product.entity';
import { WarehouseModule } from './warehouse/warehouse.module';
import { StockReservation } from './warehouse/entities/stock-reservation.entity';
import { ScheduleModule } from '@nestjs/schedule';
import { Outbox } from './outbox/entities/outbox.entity';

@Module({
  imports: [
    TypeOrmModule.forRoot({
      type: 'postgres',
      host: 'localhost',
      port: 5433,
      username: 'user',
      password: 'password',
      database: 'warehouse',
      entities: [Product, StockReservation, Outbox],
      synchronize: true,
    }),
    WarehouseModule,
    ScheduleModule.forRoot(),
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
