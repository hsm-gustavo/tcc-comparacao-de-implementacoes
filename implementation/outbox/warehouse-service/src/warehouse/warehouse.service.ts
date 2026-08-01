import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { CreateProductDto } from './dto/create-product.dto';
import { InjectRepository } from '@nestjs/typeorm';
import { Product } from './entities/product.entity';
import { EntityManager, Repository } from 'typeorm';
import { ReserveStockDto } from './dto/reserve-stock.dto';
import { ReleaseStockDto } from './dto/release-stock.dto';
import {
  StockReservation,
  StockReservationStatus,
} from './entities/stock-reservation.entity';
import { OutboxService } from 'src/outbox/outbox.service';

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
  private readonly billingQueue = 'billing-saga-queue';
  private readonly orderQueue = 'order-saga-queue';

  constructor(
    @InjectRepository(Product) private productsRepository: Repository<Product>,
    @InjectRepository(StockReservation)
    private stockReservationRepository: Repository<StockReservation>,
    private readonly outboxService: OutboxService,
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

  async reserveForSaga(payload: WarehouseSagaPayload, manager: EntityManager) {
    const existingReservation = await manager.findOneBy(StockReservation, {
      orderId: payload.orderId,
    });

    if (existingReservation) {
      return { reservation: existingReservation, created: false };
    }

    const product = await manager.findOneBy(Product, {
      id: payload.productId,
    });

    if (!product) {
      throw new NotFoundException('Product not found');
    }

    if (product.stockQuantity < payload.quantity) {
      throw new BadRequestException('Insufficient stock');
    }

    const updateResult = await manager
      .createQueryBuilder()
      .update(Product)
      .set({
        stockQuantity: () => `"stockQuantity" - ${payload.quantity}`,
      })
      .where('id = :id AND "stockQuantity" >= :quantity', {
        id: payload.productId,
        quantity: payload.quantity,
      })
      .execute();

    if (updateResult.affected === 0) {
      throw new BadRequestException('Insufficient stock');
    }

    const reservation = manager.create(StockReservation, {
      orderId: payload.orderId,
      productId: payload.productId,
      quantity: payload.quantity,
      status: StockReservationStatus.RESERVED,
    });

    //await manager.save(Product, product);
    await manager.save(StockReservation, reservation);

    return { reservation, created: true };
  }

  async releaseReservation(orderId: string, manager: EntityManager) {
    const reservation = await manager.findOneBy(StockReservation, {
      orderId,
    });

    if (
      !reservation ||
      reservation.status === StockReservationStatus.RELEASED
    ) {
      return reservation;
    }

    const product = await manager.findOneBy(Product, {
      id: reservation.productId,
    });

    if (product) {
      product.stockQuantity += reservation.quantity;
      await manager.save(Product, product);
    }

    // throw new Error('Simulated compensation failure');

    reservation.status = StockReservationStatus.RELEASED;
    return manager.save(StockReservation, reservation);
  }

  async onOrderCreated(payload: WarehouseSagaPayload) {
    return this.productsRepository.manager.transaction(
      async (transactionalEntityManager) => {
        try {
          const { reservation, created } = await this.reserveForSaga(
            payload,
            transactionalEntityManager,
          );

          if (created) {
            await this.outboxService.put(
              this.billingQueue,
              'warehouse.stock_reserved',
              {
                orderId: payload.orderId,
                customerId: payload.customerId,
                productId: payload.productId,
                quantity: payload.quantity,
                amount: payload.amount,
              },
              transactionalEntityManager,
            );
          }

          return reservation;
        } catch (error) {
          await this.outboxService.put(
            this.orderQueue,
            'warehouse.stock_failed',
            {
              orderId: payload.orderId,
              customerId: payload.customerId,
              productId: payload.productId,
              quantity: payload.quantity,
              amount: payload.amount,
              reason: String(error),
            },
            transactionalEntityManager,
          );

          throw error;
        }
      },
    );
  }

  async onSagaFailure(payload: WarehouseSagaPayload) {
    return this.productsRepository.manager.transaction(
      async (transactionalEntityManager) => {
        return this.releaseReservation(
          payload.orderId,
          transactionalEntityManager,
        );
      },
    );
  }

  async remove(id: string) {
    const product = await this.findOne(id);
    return this.productsRepository.remove(product);
  }
}
