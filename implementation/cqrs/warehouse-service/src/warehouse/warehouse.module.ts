import { Module } from '@nestjs/common';
import { WarehouseController } from './warehouse.controller';
import { Product } from './entities/product.entity';
import { TypeOrmModule } from '@nestjs/typeorm';
import { WarehouseEvent } from './entities/warehouse-event.entity';
import { WarehouseProjector } from './warehouse.projector';
import { WarehouseQueryService } from './warehouse.query.service';
import { WarehouseCommandService } from './warehouse.command.service';
import { WarehouseListener } from './warehouse.listener';

@Module({
  imports: [TypeOrmModule.forFeature([Product, WarehouseEvent])],
  controllers: [WarehouseController, WarehouseProjector, WarehouseListener],
  providers: [WarehouseCommandService, WarehouseQueryService],
})
export class WarehouseModule {}
