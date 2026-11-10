import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Order } from './order/entities/order.entity';
import { OrderItem } from './order/entities/order-item.entity';
import { OrderModule } from './order/order.module';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { DiagnosticsModule } from './diagnostics/diagnostics.module';

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
        host: configService.get<string>('POSTGRES_HOST', 'order-db'),
        port: configService.get<number>('POSTGRES_PORT', 5432),
        username: 'user',
        password: 'password',
        database: 'order',
        entities: [Order, OrderItem],
        synchronize: true,
        extra: {
          max: Number(process.env.DB_POOL_MAX ?? 80),
          connectionTimeoutMillis: 2000,
          idleTimeoutMillis: 30000,
        },
      }),
    }),
    TypeOrmModule.forFeature([Order, OrderItem]),
    OrderModule,
    DiagnosticsModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
