import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Payment } from './billing/entities/payment.entity';
import { BillingModule } from './billing/billing.module';
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
        host: configService.get<string>('POSTGRES_HOST', 'billing-db'),
        port: configService.get<number>('POSTGRES_PORT', 5432),
        username: 'user',
        password: 'password',
        database: 'billing',
        entities: [Payment],
        synchronize: true,
      }),
    }),
    TypeOrmModule.forFeature([Payment]),
    BillingModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
