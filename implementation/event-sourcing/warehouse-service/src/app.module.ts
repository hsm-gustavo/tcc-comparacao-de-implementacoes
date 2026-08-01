import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Product } from './warehouse/entities/product.entity';
import { WarehouseModule } from './warehouse/warehouse.module';
import { WarehouseEvent } from './warehouse/entities/warehouse-event.entity';

@Module({
  imports: [
    TypeOrmModule.forRoot({
      type: 'postgres',
      host: 'localhost',
      port: 5433,
      username: 'user',
      password: 'password',
      database: 'warehouse',
      entities: [Product, WarehouseEvent],
      synchronize: true,
    }),
    WarehouseModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
