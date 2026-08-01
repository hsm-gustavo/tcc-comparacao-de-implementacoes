import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Payment } from './billing/entities/payment.entity';
import { BillingModule } from './billing/billing.module';
import { BillingEvent } from './billing/entities/billing-event.entity';

@Module({
  imports: [
    TypeOrmModule.forRoot({
      type: 'postgres',
      host: 'localhost',
      port: 5435,
      username: 'user',
      password: 'password',
      database: 'billing',
      entities: [Payment, BillingEvent],
      synchronize: true,
    }),
    BillingModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
