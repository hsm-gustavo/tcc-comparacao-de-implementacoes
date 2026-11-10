import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Product } from './warehouse/entities/product.entity';
import { WarehouseModule } from './warehouse/warehouse.module';
import { StockReservation } from './warehouse/entities/stock-reservation.entity';

@Module({
  imports: [
    TypeOrmModule.forRoot({
      type: 'postgres',
      host: process.env.POSTGRES_HOST ?? 'localhost',
      port: Number(process.env.POSTGRES_PORT ?? 5433),
      username: 'user',
      password: 'password',
      database: 'warehouse',
      entities: [Product, StockReservation],
      synchronize: true,
      extra: {
        max: Number(process.env.DB_POOL_MAX ?? 80),
        connectionTimeoutMillis: 2000,
        idleTimeoutMillis: 30000,
      },
    }),
    TypeOrmModule.forFeature([Product, StockReservation]),
    WarehouseModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
