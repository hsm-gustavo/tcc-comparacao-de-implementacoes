import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Shipment } from './shipping/entities/shipment.entity';
import { ShippingModule } from './shipping/shipping.module';
import { ScheduleModule } from '@nestjs/schedule';
import { Outbox } from './outbox/entities/outbox.entity';

@Module({
  imports: [
    TypeOrmModule.forRoot({
      type: 'postgres',
      host: 'localhost',
      port: 5436,
      username: 'user',
      password: 'password',
      database: 'shipping',
      entities: [Shipment, Outbox],
      synchronize: true,
    }),
    ShippingModule,
    ScheduleModule.forRoot(),
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
