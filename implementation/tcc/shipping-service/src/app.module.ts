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
      host: 'localhost',
      port: 5436,
      username: 'user',
      password: 'password',
      database: 'shipping',
      entities: [Shipment],
      synchronize: true,
    }),
    TypeOrmModule.forFeature([Shipment]),
    ShippingModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
