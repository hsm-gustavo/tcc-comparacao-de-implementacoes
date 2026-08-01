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

  private async getReservedQuantity(productId: string) {
    const reservations = await this.stockReservationRepository.find({
      where: { productId, status: ReservationStatus.PREPARED },
    });

    return reservations.reduce((total, reservation) => {
      return total + reservation.quantity;
    }, 0);
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

  async prepareStockReservation(dto: PrepareStockReservationDto) {
    const existingReservation = await this.stockReservationRepository.findOneBy(
      {
        orderId: dto.orderId,
      },
    );

    if (existingReservation) {
      return existingReservation;
    }

    const product = await this.productsRepository.findOneBy({
      id: dto.productId,
    });

    if (!product) throw new NotFoundException('Product not found');

    const reservedQuantity = await this.getReservedQuantity(dto.productId);

    if (product.stockQuantity - reservedQuantity < dto.quantity) {
      throw new BadRequestException('Insufficient stock');
    }

    const reservation = this.stockReservationRepository.create({
      orderId: dto.orderId,
      productId: dto.productId,
      quantity: dto.quantity,
      status: ReservationStatus.PREPARED,
    });

    return this.stockReservationRepository.save(reservation);
  }

  async commitStockReservation(dto: StockReservationActionDto) {
    const reservation = await this.stockReservationRepository.findOneBy({
      orderId: dto.orderId,
    });

    if (!reservation) {
      throw new NotFoundException('Stock reservation not found');
    }

    if (reservation.status === ReservationStatus.COMMITTED) {
      return reservation;
    }

    if (reservation.status === ReservationStatus.CANCELLED) {
      throw new BadRequestException(
        'Stock reservation was already rolled back',
      );
    }

    const product = await this.productsRepository.findOneBy({
      id: reservation.productId,
    });

    if (!product) throw new NotFoundException('Product not found');

    if (product.stockQuantity < reservation.quantity) {
      throw new BadRequestException('Insufficient stock');
    }

    product.stockQuantity -= reservation.quantity;
    reservation.status = ReservationStatus.COMMITTED;

    await this.productsRepository.save(product);
    return this.stockReservationRepository.save(reservation);
  }

  async rollbackStockReservation(dto: StockReservationActionDto) {
    const reservation = await this.stockReservationRepository.findOneBy({
      orderId: dto.orderId,
    });

    if (!reservation) {
      return null;
    }

    if (reservation.status === ReservationStatus.COMMITTED) {
      return reservation;
    }

    reservation.status = ReservationStatus.CANCELLED;
    return this.stockReservationRepository.save(reservation);
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

  async remove(id: string) {
    const product = await this.findOne(id);
    return this.productsRepository.remove(product);
  }
}
