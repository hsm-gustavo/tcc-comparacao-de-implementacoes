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
import { SagaPublisher } from '../saga-publisher';

type WarehouseSagaPayload = {
  orderId: string;
  customerId?: string;
  productId: string;
  quantity: number;
  amount?: number;
  reason?: string;
};

@Injectable()
export class WarehouseService extends SagaPublisher {
  private readonly billingQueue = 'billing-saga-queue';
  private readonly orderQueue = 'order-saga-queue';

  constructor(
    @InjectRepository(Product) private productsRepository: Repository<Product>,
    @InjectRepository(StockReservation)
    private stockReservationRepository: Repository<StockReservation>,
  ) {
    super();
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
    return this.productsRepository.manager.transaction(async (manager) => {
      // Serializes duplicate deliveries, including when no reservation exists yet.
      await manager.query(
        'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
        [payload.orderId],
      );
      const existing = await manager.findOneBy(StockReservation, {
        orderId: payload.orderId,
      });
      if (existing) return { reservation: existing, created: false };
      if (!Number.isInteger(payload.quantity) || payload.quantity <= 0) {
        throw new BadRequestException('Quantity must be a positive integer');
      }
      const product = await manager.findOne(Product, {
        where: { id: payload.productId },
        lock: { mode: 'pessimistic_write' },
      });
      if (!product) throw new NotFoundException('Product not found');
      if (product.stockQuantity < payload.quantity)
        throw new BadRequestException('Insufficient stock');
      product.stockQuantity -= payload.quantity;
      const reservation = manager.create(StockReservation, {
        orderId: payload.orderId,
        productId: payload.productId,
        quantity: payload.quantity,
        status: StockReservationStatus.RESERVED,
      });
      await manager.save(Product, product);
      await manager.save(StockReservation, reservation);
      return { reservation, created: true };
    });
  }

  async releaseReservation(orderId: string) {
    return this.productsRepository.manager.transaction(async (manager) => {
      await manager.query(
        'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
        [orderId],
      );
      const reservation = await manager.findOneBy(StockReservation, {
        orderId,
      });
      if (
        !reservation ||
        reservation.status === StockReservationStatus.RELEASED
      )
        return reservation;
      const product = await manager.findOne(Product, {
        where: { id: reservation.productId },
        lock: { mode: 'pessimistic_write' },
      });
      if (product) {
        product.stockQuantity += reservation.quantity;
        await manager.save(Product, product);
      }
      reservation.status = StockReservationStatus.RELEASED;
      return manager.save(StockReservation, reservation);
    });
  }

  async onOrderCreated(payload: WarehouseSagaPayload) {
    let reservation: StockReservation;
    try {
      ({ reservation } = await this.reserveForSaga(payload));
    } catch (error) {
      if (
        !(
          error instanceof BadRequestException ||
          error instanceof NotFoundException
        )
      )
        throw error;
      await this.emit(this.orderQueue, 'warehouse.stock_failed', {
        ...payload,
        reason: String(error),
      });
      return;
    }
    // Replay the next event after a failed publication, without reserving twice.
    if (reservation.status === StockReservationStatus.RESERVED) {
      await this.emit(this.billingQueue, 'warehouse.stock_reserved', payload);
    }
    return reservation;
  }

  async onSagaFailure(payload: WarehouseSagaPayload) {
    return this.releaseReservation(payload.orderId);
  }

  async remove(id: string) {
    const product = await this.findOne(id);
    return this.productsRepository.remove(product);
  }
}
