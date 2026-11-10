import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Product } from './warehouse/entities/product.entity';
import { WarehouseModule } from './warehouse/warehouse.module';
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
        host: configService.get<string>('POSTGRES_HOST', 'warehouse-db'),
        port: configService.get<number>('POSTGRES_PORT', 5432),
        username: 'user',
        password: 'password',
        database: 'warehouse',
        entities: [Product],
        synchronize: true,
        extra: {
          max: Number(process.env.DB_POOL_MAX ?? 80),
          connectionTimeoutMillis: 2000,
          idleTimeoutMillis: 30000,
        },
      }),
    }),
    TypeOrmModule.forFeature([Product]),
    WarehouseModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
