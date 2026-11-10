import {
  Injectable,
  InternalServerErrorException,
  Logger,
} from '@nestjs/common';
import { Repository } from 'typeorm';
import { Order, OrderStatus } from './entities/order.entity';
import { OrderItem } from './entities/order-item.entity';
import { InjectRepository } from '@nestjs/typeorm';
import { HttpService } from '@nestjs/axios';
import { CreateOrderDto } from './dto/create-order.dto';
import { firstValueFrom } from 'rxjs';

@Injectable()
export class OrderService {
  private readonly logger = new Logger(OrderService.name);
  private readonly WAREHOUSE_URL =
    process.env.WAREHOUSE_URL ?? 'http://localhost:3001/warehouse';
  private readonly BILLING_URL =
    process.env.BILLING_URL ?? 'http://localhost:3002/billing';
  private readonly SHIPPING_URL =
    process.env.SHIPPING_URL ?? 'http://localhost:3003/shipping';

  constructor(
    @InjectRepository(Order) private orderRepo: Repository<Order>,
    @InjectRepository(OrderItem) private orderItemRepo: Repository<OrderItem>,
    private readonly httpService: HttpService,
  ) {}
  async createOrder(dto: CreateOrderDto) {
    const savedOrder = await this.orderRepo.manager.transaction(
      async (manager) => {
        const order = manager.create(Order, {
          customerId: dto.customerId,
          totalAmount: dto.amount,
        });
        const saved = await manager.save(Order, order);

        const item = manager.create(OrderItem, {
          order: saved,
          productId: dto.productId,
          quantity: dto.quantity,
        });
        await manager.save(OrderItem, item);

        return saved;
      },
    );

    let confirmationStarted = false;
    try {
      // Phase 1: TCC Try on all participants
      await firstValueFrom(
        this.httpService.patch(`${this.WAREHOUSE_URL}/try-reserve`, {
          orderId: savedOrder.id,
          productId: dto.productId,
          quantity: dto.quantity,
        }),
      );

      await firstValueFrom(
        this.httpService.post(`${this.BILLING_URL}/try`, {
          orderId: savedOrder.id,
          amount: dto.amount,
        }),
      );

      await firstValueFrom(
        this.httpService.post(`${this.SHIPPING_URL}/try`, {
          orderId: savedOrder.id,
        }),
      );

      // Phase 2: TCC Confirm on all participants
      confirmationStarted = true;
      await firstValueFrom(
        this.httpService.patch(`${this.WAREHOUSE_URL}/confirm-reserve`, {
          orderId: savedOrder.id,
        }),
      );

      await firstValueFrom(
        this.httpService.post(`${this.BILLING_URL}/confirm`, {
          orderId: savedOrder.id,
        }),
      );

      await firstValueFrom(
        this.httpService.post(`${this.SHIPPING_URL}/confirm`, {
          orderId: savedOrder.id,
        }),
      );

      savedOrder.status = OrderStatus.CONFIRMED;
      return await this.orderRepo.save(savedOrder);
    } catch (err) {
      if (confirmationStarted) {
        // An uncertain Confirm/Commit is not a new decision to Cancel/Rollback.
        this.logger.error({
          event: 'coordination.unresolved',
          orderId: savedOrder.id,
          phase: 'confirmation',
          reason: String(err),
        });
        throw new InternalServerErrorException(
          `Order confirmation outcome unknown: ${String(err)}`,
        );
      }
      // Best-effort cancel on all participants
      const cancellations = await Promise.allSettled([
        firstValueFrom(
          this.httpService.post(`${this.SHIPPING_URL}/cancel`, {
            orderId: savedOrder.id,
          }),
        ),
        firstValueFrom(
          this.httpService.post(`${this.BILLING_URL}/cancel`, {
            orderId: savedOrder.id,
          }),
        ),
        firstValueFrom(
          this.httpService.patch(`${this.WAREHOUSE_URL}/cancel-reserve`, {
            orderId: savedOrder.id,
          }),
        ),
      ]);

      if (cancellations.some((result) => result.status === 'rejected')) {
        this.logger.error({
          event: 'coordination.unresolved',
          orderId: savedOrder.id,
          phase: 'cancellation',
          reason: String(err),
        });
        throw new InternalServerErrorException(
          `Order cancellation outcome unknown: ${String(err)}`,
        );
      }
      savedOrder.status = OrderStatus.CANCELLED;
      await this.orderRepo.save(savedOrder);

      throw new InternalServerErrorException(
        `Distributed Order failed: ${err}`,
      );
    }
  }
}
