import { Injectable, InternalServerErrorException } from '@nestjs/common';
import { Repository } from 'typeorm';
import { Order, OrderStatus } from './entities/order.entity';
import { OrderItem } from './entities/order-item.entity';
import { InjectRepository } from '@nestjs/typeorm';
import { HttpService } from '@nestjs/axios';
import { CreateOrderDto } from './dto/create-order.dto';
import { firstValueFrom } from 'rxjs';

@Injectable()
export class OrderService {
  private readonly WAREHOUSE_URL = 'http://localhost:3001/warehouse';
  private readonly BILLING_URL = 'http://localhost:3002/billing';
  private readonly SHIPPING_URL = 'http://localhost:3003/shipping';

  constructor(
    @InjectRepository(Order) private orderRepo: Repository<Order>,
    @InjectRepository(OrderItem) private orderItemRepo: Repository<OrderItem>,
    private readonly httpService: HttpService,
  ) {}
  async createOrder(dto: CreateOrderDto) {
    const order = this.orderRepo.create({
      customerId: dto.customerId,
      totalAmount: dto.amount,
    });
    const savedOrder = await this.orderRepo.save(order);

    const item = this.orderItemRepo.create({
      order: savedOrder,
      productId: dto.productId,
      quantity: dto.quantity,
    });
    await this.orderItemRepo.save(item);

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
      // Best-effort cancel on all participants
      await Promise.allSettled([
        firstValueFrom(
          this.httpService.post(`${this.SHIPPING_URL}/cancel`, {
            orderId: savedOrder.id,
          }),
        ).catch(() => null),
        firstValueFrom(
          this.httpService.post(`${this.BILLING_URL}/cancel`, {
            orderId: savedOrder.id,
          }),
        ).catch(() => null),
        firstValueFrom(
          this.httpService.patch(`${this.WAREHOUSE_URL}/cancel-reserve`, {
            orderId: savedOrder.id,
          }),
        ).catch(() => null),
      ]);

      savedOrder.status = OrderStatus.CANCELLED;
      await this.orderRepo.save(savedOrder);

      throw new InternalServerErrorException(
        `Distributed Order failed: ${err}`,
      );
    }
  }
}
