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
import { PrepareStockReservationDto } from './dto/prepare-stock-reservation.dto';
import {
  ReservationStatus,
  StockReservation,
} from './entities/stock-reservation.entity';
import { StockReservationActionDto } from './dto/stock-reservation-action.dto';

@Injectable()
export class WarehouseService {
  constructor(
    @InjectRepository(Product) private productsRepository: Repository<Product>,
    @InjectRepository(StockReservation)
    private stockReservationRepository: Repository<StockReservation>,
  ) {}

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
    if (!Number.isInteger(dto.quantity) || dto.quantity <= 0)
      throw new BadRequestException('Quantity must be a positive integer');
    const result = await this.productsRepository
      .createQueryBuilder()
      .update()
      .set({ stockQuantity: () => '"stockQuantity" - :quantity' })
      .where('id = :productId', { productId: dto.productId })
      .andWhere('"stockQuantity" >= :quantity')
      .setParameters({ quantity: dto.quantity })
      .execute();
    if (result.affected === 1) return this.findOne(dto.productId);
    await this.findOne(dto.productId);
    throw new BadRequestException('Insufficient stock');
  }

  async prepareStockReservation(dto: PrepareStockReservationDto) {
    if (!Number.isInteger(dto.quantity) || dto.quantity <= 0)
      throw new BadRequestException('Quantity must be a positive integer');
    return this.productsRepository.manager.transaction(async (manager) => {
      await manager.query(
        'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
        [dto.orderId],
      );
      const existing = await manager.findOneBy(StockReservation, {
        orderId: dto.orderId,
      });
      if (existing) {
        if (existing.status === ReservationStatus.CANCELLED)
          throw new BadRequestException('Reservation cancelled');
        return existing;
      }
      const product = await manager.findOne(Product, {
        where: { id: dto.productId },
        lock: { mode: 'pessimistic_write' },
      });
      if (!product) throw new NotFoundException('Product not found');
      const reservations = await manager.find(StockReservation, {
        where: { productId: dto.productId, status: ReservationStatus.PREPARED },
      });
      const reserved = reservations.reduce((sum, row) => sum + row.quantity, 0);
      if (product.stockQuantity - reserved < dto.quantity)
        throw new BadRequestException('Insufficient stock');
      return manager.save(
        StockReservation,
        manager.create(StockReservation, {
          orderId: dto.orderId,
          productId: dto.productId,
          quantity: dto.quantity,
          status: ReservationStatus.PREPARED,
        }),
      );
    });
  }

  async commitStockReservation(dto: StockReservationActionDto) {
    return this.productsRepository.manager.transaction(async (manager) => {
      await manager.query(
        'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
        [dto.orderId],
      );
      const reservation = await manager.findOneBy(StockReservation, {
        orderId: dto.orderId,
      });
      if (!reservation)
        throw new NotFoundException('Stock reservation not found');
      if (reservation.status === ReservationStatus.COMMITTED)
        return reservation;
      if (reservation.status === ReservationStatus.CANCELLED)
        throw new BadRequestException('Reservation cancelled');
      const product = await manager.findOne(Product, {
        where: { id: reservation.productId },
        lock: { mode: 'pessimistic_write' },
      });
      if (!product) throw new NotFoundException('Product not found');
      if (product.stockQuantity < reservation.quantity)
        throw new BadRequestException('Insufficient stock');
      product.stockQuantity -= reservation.quantity;
      reservation.status = ReservationStatus.COMMITTED;
      await manager.save(Product, product);
      return manager.save(StockReservation, reservation);
    });
  }

  async rollbackStockReservation(dto: StockReservationActionDto) {
    return this.productsRepository.manager.transaction(async (manager) => {
      await manager.query(
        'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
        [dto.orderId],
      );
      const reservation = await manager.findOneBy(StockReservation, {
        orderId: dto.orderId,
      });
      if (!reservation) {
        // Empty cancel: persist the terminal decision so a late Try/Prepare cannot reserve.
        return manager.save(
          StockReservation,
          manager.create(StockReservation, {
            orderId: dto.orderId,
            productId: '',
            quantity: 0,
            status: ReservationStatus.CANCELLED,
          }),
        );
      }
      if (reservation.status === ReservationStatus.COMMITTED)
        throw new BadRequestException('Reservation already committed');
      if (reservation.status === ReservationStatus.CANCELLED)
        return reservation;
      // Same product lock as Prepare/Confirm, so availability is a consistent snapshot.
      await manager.findOne(Product, {
        where: { id: reservation.productId },
        lock: { mode: 'pessimistic_write' },
      });
      reservation.status = ReservationStatus.CANCELLED;
      return manager.save(StockReservation, reservation);
    });
  }

  async releaseStock(dto: ReleaseStockDto) {
    if (!Number.isInteger(dto.quantity) || dto.quantity <= 0)
      throw new BadRequestException('Quantity must be a positive integer');
    await this.productsRepository
      .createQueryBuilder()
      .update()
      .set({ stockQuantity: () => '"stockQuantity" + :quantity' })
      .where('id = :productId', { productId: dto.productId })
      .setParameters({ quantity: dto.quantity })
      .execute();
  }

  async remove(id: string) {
    const product = await this.findOne(id);
    return this.productsRepository.remove(product);
  }
}
