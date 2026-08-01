import { Module } from '@nestjs/common';
import { WarehouseService } from './warehouse.service';
import { WarehouseController } from './warehouse.controller';
import { Product } from './entities/product.entity';
import { StockReservation } from './entities/stock-reservation.entity';
import { TypeOrmModule } from '@nestjs/typeorm';
import { OutboxModule } from 'src/outbox/outbox.module';

@Module({
  imports: [
    TypeOrmModule.forFeature([Product, StockReservation]),
    OutboxModule,
  ],
  controllers: [WarehouseController],
  providers: [WarehouseService],
  exports: [WarehouseService],
})
export class WarehouseModule {}
