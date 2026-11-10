import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Payment } from './billing/entities/payment.entity';
import { BillingModule } from './billing/billing.module';

@Module({
  imports: [
    TypeOrmModule.forRoot({
      type: 'postgres',
      host: process.env.POSTGRES_HOST ?? 'localhost',
      port: Number(process.env.POSTGRES_PORT ?? 5435),
      username: 'user',
      password: 'password',
      database: 'billing',
      entities: [Payment],
      synchronize: true,
      extra: {
        max: Number(process.env.DB_POOL_MAX ?? 80),
        connectionTimeoutMillis: 2000,
        idleTimeoutMillis: 30000,
      },
    }),
    TypeOrmModule.forFeature([Payment]),
    BillingModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
