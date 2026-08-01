import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Shipment } from './shipping/entities/shipment.entity';
import { ShippingModule } from './shipping/shipping.module';
import { ConfigModule, ConfigService } from '@nestjs/config';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
    }),
    TypeOrmModule.forRootAsync({
      imports: [ConfigModule],
      inject: [ConfigService],
      useFactory: (configService: ConfigService) => ({
        type: 'postgres',
        host: configService.get<string>('POSTGRES_HOST', 'shipping-db'),
        port: configService.get<number>('POSTGRES_PORT', 5432),
        username: 'user',
        password: 'password',
        database: 'shipping',
        entities: [Shipment],
        synchronize: true,
      }),
    }),
    TypeOrmModule.forFeature([Shipment]),
    ShippingModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
