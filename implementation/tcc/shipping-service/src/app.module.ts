import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Shipment } from './shipping/entities/shipment.entity';
import { ShippingModule } from './shipping/shipping.module';

@Module({
  imports: [
    TypeOrmModule.forRoot({
      type: 'postgres',
      host: process.env.POSTGRES_HOST ?? 'localhost',
      port: Number(process.env.POSTGRES_PORT ?? 5436),
      username: 'user',
      password: 'password',
      database: 'shipping',
      entities: [Shipment],
      synchronize: true,
      extra: {
        max: Number(process.env.DB_POOL_MAX ?? 80),
        connectionTimeoutMillis: 2000,
        idleTimeoutMillis: 30000,
      },
    }),
    TypeOrmModule.forFeature([Shipment]),
    ShippingModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
