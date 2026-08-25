import {
  BadRequestException,
  Injectable,
  InternalServerErrorException,
  Logger,
} from '@nestjs/common';
import { DataSource } from 'typeorm';
import { WarehouseService } from 'src/warehouse/warehouse.service';
import { BillingService } from 'src/billing/billing.service';
import { ShippingService } from 'src/shipping/shipping.service';
import { Order, OrderStatus } from './entities/order.entity';
import { Product } from 'src/warehouse/entities/product.entity';
import { OrderItem } from './entities/order-item.entity';

const ENABLE_SLOW_LOGS = false;

@Injectable()
export class OrderService {
  private readonly logger = new Logger(OrderService.name);

  constructor(
    private dataSource: DataSource,
    private warehouseService: WarehouseService,
    private billingService: BillingService,
    private shippingService: ShippingService,
  ) {}
  async createOrder(
    customerId: string,
    productId: string,
    quantity: number,
    clientAmount?: number,
  ) {
    const startedAt = performance.now();
    const queryRunner = this.dataSource.createQueryRunner();
    await this.measure('database.transaction.connect', () =>
      queryRunner.connect(),
    );
    await this.measure('database.transaction.start', () =>
      queryRunner.startTransaction(),
    );

    try {
      const product = await this.measure(
        'database.product.find',
        () =>
          queryRunner.manager.findOne(Product, {
            where: { id: productId },
          }),
        { productId },
      );

      if (!product) throw new BadRequestException('Product not found');

      const calculatedTotal = Number((product.price * quantity).toFixed(2));

      if (
        clientAmount !== undefined &&
        Math.abs(clientAmount - calculatedTotal) > 0.01
      )
        throw new BadRequestException(
          `Invalid amount. Expected ${calculatedTotal}, but received ${clientAmount}`,
        );

      // tenta decrementar o estoque atomicamente
      // note que isso só é possível pq estamos em um monolito
      const updateResult = await this.measure(
        'database.product.stock-decrement',
        () =>
          queryRunner.manager
            .createQueryBuilder()
            .update(Product)
            .set({ stockQuantity: () => `stockQuantity - ${quantity}` })
            .where('id = :id AND stockQuantity >= :quantity', {
              id: productId,
              quantity,
            })
            .execute(),
        { productId, quantity },
      );

      if (updateResult.affected === 0) {
        throw new BadRequestException(
          'Insufficient stock or product unavailable',
        );
      }

      const order = queryRunner.manager.create(Order, {
        customerId,
        totalAmount: calculatedTotal,
        status: OrderStatus.PENDING,
      });
      const savedOrder = await this.measure(
        'database.order.save',
        () => queryRunner.manager.save(order),
        { productId, customerId },
      );

      const orderItem = queryRunner.manager.create(OrderItem, {
        order: savedOrder,
        product: product,
        quantity: quantity,
      });
      await this.measure(
        'database.order-item.save',
        () => queryRunner.manager.save(orderItem),
        { orderId: savedOrder.id, productId },
      );

      await this.measure(
        'in-process.warehouse.reserve',
        () =>
          this.warehouseService.reserveStock(
            productId,
            quantity,
            queryRunner.manager,
          ),
        { orderId: savedOrder.id, productId },
      );

      await this.measure(
        'in-process.billing.process',
        () =>
          this.billingService.processPayment(
            savedOrder,
            calculatedTotal,
            queryRunner.manager,
          ),
        { orderId: savedOrder.id, productId },
      );

      await this.measure(
        'in-process.shipping.create',
        () =>
          this.shippingService.createShipment(savedOrder, queryRunner.manager),
        { orderId: savedOrder.id, productId },
      );

      savedOrder.status = OrderStatus.CONFIRMED;
      await this.measure(
        'database.order.confirm',
        () => queryRunner.manager.save(savedOrder),
        { orderId: savedOrder.id },
      );

      await this.measure('database.transaction.commit', () =>
        queryRunner.commitTransaction(),
      );

      const durationMs = this.elapsed(startedAt);
      if (ENABLE_SLOW_LOGS && durationMs >= 8000) {
        this.logger.log({
          event: 'monolith-order.completed',
          orderId: savedOrder.id,
          productId,
          durationMs,
        });
      }

      return savedOrder;
    } catch (error: unknown) {
      await this.measure('database.transaction.rollback', () =>
        queryRunner.rollbackTransaction(),
      );

      const details = this.extractErrorDetails(error);
      this.logger.error({
        event: 'monolith-order.failed',
        productId,
        durationMs: this.elapsed(startedAt),
        ...details,
      });

      if (error instanceof BadRequestException) {
        throw error;
      }
      throw new InternalServerErrorException(
        `Order failed: ${details.message}`,
      );
    } finally {
      await queryRunner.release();
    }
  }

  private async measure<T>(
    operation: string,
    callback: () => Promise<T>,
    context: Record<string, unknown> = {},
  ): Promise<T> {
    const startedAt = performance.now();

    try {
      const result = await callback();
      const durationMs = this.elapsed(startedAt);

      if (ENABLE_SLOW_LOGS && durationMs >= 8000) {
        this.logger.log({
          event: 'operation.completed',
          operation,
          durationMs,
          ...context,
        });
      }

      return result;
    } catch (error: unknown) {
      const details = this.extractErrorDetails(error);

      this.logger.error({
        event: 'operation.failed',
        operation,
        durationMs: this.elapsed(startedAt),
        ...context,
        ...details,
      });

      throw error;
    }
  }

  private extractErrorDetails(error: unknown) {
    if (error instanceof Error) {
      return {
        type: 'error',
        message: error.message,
        errorName: error.name,
      };
    }

    return {
      type: 'unknown',
      message: String(error),
    };
  }

  private elapsed(startedAt: number): number {
    return Number((performance.now() - startedAt).toFixed(2));
  }
}
