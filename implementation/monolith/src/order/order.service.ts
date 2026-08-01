import {
  BadRequestException,
  Injectable,
  InternalServerErrorException,
} from '@nestjs/common';
import { DataSource } from 'typeorm';
import { WarehouseService } from 'src/warehouse/warehouse.service';
import { BillingService } from 'src/billing/billing.service';
import { ShippingService } from 'src/shipping/shipping.service';
import { Order, OrderStatus } from './entities/order.entity';
import { Product } from 'src/warehouse/entities/product.entity';
import { OrderItem } from './entities/order-item.entity';

@Injectable()
export class OrderService {
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
    const queryRunner = this.dataSource.createQueryRunner();
    await queryRunner.connect();
    await queryRunner.startTransaction();

    try {
      const product = await queryRunner.manager.findOne(Product, {
        where: { id: productId },
      });

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
      const updateResult = await queryRunner.manager
        .createQueryBuilder()
        .update(Product)
        .set({ stockQuantity: () => `stockQuantity - ${quantity}` })
        .where('id = :id AND stockQuantity >= :quantity', {
          id: productId,
          quantity: quantity,
        })
        .execute();

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
      const savedOrder = await queryRunner.manager.save(order);

      const orderItem = queryRunner.manager.create(OrderItem, {
        order: savedOrder,
        product: product,
        quantity: quantity,
      });
      await queryRunner.manager.save(orderItem);

      await this.warehouseService.reserveStock(
        productId,
        quantity,
        queryRunner.manager,
      );

      await this.billingService.processPayment(
        savedOrder,
        calculatedTotal,
        queryRunner.manager,
      );

      await this.shippingService.createShipment(
        savedOrder,
        queryRunner.manager,
      );

      savedOrder.status = OrderStatus.CONFIRMED;
      await queryRunner.manager.save(savedOrder);

      await queryRunner.commitTransaction();
      return savedOrder;
    } catch (err) {
      await queryRunner.rollbackTransaction();

      if (err instanceof BadRequestException) {
        throw err;
      }
      throw new InternalServerErrorException(`Order failed: ${err}`);
    } finally {
      await queryRunner.release();
    }
  }
}
