import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { CreateProductDto } from './dto/create-product.dto';
import { InjectRepository } from '@nestjs/typeorm';
import { Product } from './entities/product.entity';
import { Repository } from 'typeorm';
import { ReserveStockDto } from './dto/reserve-stock.dto';
import { ReleaseStockDto } from './dto/release-stock.dto';
import {
  StockReservation,
  StockReservationStatus,
} from './entities/stock-reservation.entity';
import {
  ClientProxy,
  ClientProxyFactory,
  Transport,
} from '@nestjs/microservices';

type WarehouseSagaPayload = {
  orderId: string;
  customerId?: string;
  productId: string;
  quantity: number;
  amount?: number;
  reason?: string;
};

@Injectable()
export class WarehouseService {
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';
  private readonly billingQueue = 'billing-saga-queue';
  private readonly orderQueue = 'order-saga-queue';

  constructor(
    @InjectRepository(Product) private productsRepository: Repository<Product>,
    @InjectRepository(StockReservation)
    private stockReservationRepository: Repository<StockReservation>,
  ) {}

  private createClient(queue: string): ClientProxy {
    return ClientProxyFactory.create({
      transport: Transport.RMQ,
      options: {
        urls: [this.rabbitUrl],
        queue,
        queueOptions: {
          durable: true,
        },
      },
    });
  }

  private async emit(
    queue: string,
    pattern: string,
    payload: WarehouseSagaPayload,
  ) {
    const client = this.createClient(queue);

    try {
      await new Promise<void>((resolve, reject) => {
        client.emit(pattern, payload).subscribe({
          complete: resolve,
          error: reject,
        });
      });
    } finally {
      await client.close();
    }
  }

  async create(createProductDto: CreateProductDto) {
    const product = this.productsRepository.create(createProductDto);
    return this.productsRepository.save(product);
  }

  async findAll() {
    return this.productsRepository.find();
  }

  async findOne(id: string) {
    const product = await this.productsRepository.findOne({
      where: { id },
    });
    if (!product) throw new NotFoundException('Product not found');
    return product;
  }

  async reserveStock(dto: ReserveStockDto) {
    const product = await this.productsRepository.findOneBy({
      id: dto.productId,
    });

    if (!product) throw new NotFoundException('Product not found');

    if (product.stockQuantity < dto.quantity) {
      throw new BadRequestException('Insufficient stock');
    }

    product.stockQuantity -= dto.quantity;
    return this.productsRepository.save(product);
  }

  async releaseStock(dto: ReleaseStockDto) {
    const product = await this.productsRepository.findOneBy({
      id: dto.productId,
    });
    if (product) {
      product.stockQuantity += dto.quantity;
      await this.productsRepository.save(product);
    }
  }

  async reserveForSaga(payload: WarehouseSagaPayload) {
    const existingReservation = await this.stockReservationRepository.findOneBy(
      {
        orderId: payload.orderId,
      },
    );

    if (existingReservation) {
      return { reservation: existingReservation, created: false };
    }

    const product = await this.productsRepository.findOneBy({
      id: payload.productId,
    });

    if (!product) {
      throw new NotFoundException('Product not found');
    }

    if (product.stockQuantity < payload.quantity) {
      throw new BadRequestException('Insufficient stock');
    }

    product.stockQuantity -= payload.quantity;

    const reservation = this.stockReservationRepository.create({
      orderId: payload.orderId,
      productId: payload.productId,
      quantity: payload.quantity,
      status: StockReservationStatus.RESERVED,
    });

    await this.productsRepository.save(product);
    await this.stockReservationRepository.save(reservation);

    return { reservation, created: true };
  }

  async releaseReservation(orderId: string) {
    const reservation = await this.stockReservationRepository.findOneBy({
      orderId,
    });

    if (
      !reservation ||
      reservation.status === StockReservationStatus.RELEASED
    ) {
      return reservation;
    }

    const product = await this.productsRepository.findOneBy({
      id: reservation.productId,
    });

    if (product) {
      product.stockQuantity += reservation.quantity;
      await this.productsRepository.save(product);
    }

    reservation.status = StockReservationStatus.RELEASED;
    return this.stockReservationRepository.save(reservation);
  }

  async onOrderCreated(payload: WarehouseSagaPayload) {
    try {
      const { reservation, created } = await this.reserveForSaga(payload);

      if (created) {
        await this.emit(this.billingQueue, 'warehouse.stock_reserved', {
          orderId: payload.orderId,
          customerId: payload.customerId,
          productId: payload.productId,
          quantity: payload.quantity,
          amount: payload.amount,
        });
      }

      return reservation;
    } catch (error) {
      await this.emit(this.orderQueue, 'warehouse.stock_failed', {
        orderId: payload.orderId,
        customerId: payload.customerId,
        productId: payload.productId,
        quantity: payload.quantity,
        amount: payload.amount,
        reason: String(error),
      });

      throw error;
    }
  }

  async onSagaFailure(payload: WarehouseSagaPayload) {
    return this.releaseReservation(payload.orderId);
  }

  async remove(id: string) {
    const product = await this.findOne(id);
    return this.productsRepository.remove(product);
  }
}
