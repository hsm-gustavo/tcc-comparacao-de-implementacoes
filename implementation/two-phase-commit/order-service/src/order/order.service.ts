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
      // 2. Phase 1: prepare all participants
      await firstValueFrom(
        this.httpService.patch(`${this.WAREHOUSE_URL}/prepare-reserve`, {
          orderId: savedOrder.id,
          productId: dto.productId,
          quantity: dto.quantity,
        }),
      );

      await firstValueFrom(
        this.httpService.post(`${this.BILLING_URL}/prepare`, {
          orderId: savedOrder.id,
          amount: dto.amount,
        }),
      );

      await firstValueFrom(
        this.httpService.post(`${this.SHIPPING_URL}/prepare`, {
          orderId: savedOrder.id,
        }),
      );

      // 3. Phase 2: commit all participants
      await firstValueFrom(
        this.httpService.patch(`${this.WAREHOUSE_URL}/commit-reserve`, {
          orderId: savedOrder.id,
        }),
      );

      await firstValueFrom(
        this.httpService.post(`${this.BILLING_URL}/commit`, {
          orderId: savedOrder.id,
        }),
      );

      await firstValueFrom(
        this.httpService.post(`${this.SHIPPING_URL}/commit`, {
          orderId: savedOrder.id,
        }),
      );

      savedOrder.status = OrderStatus.CONFIRMED;
      return await this.orderRepo.save(savedOrder);
    } catch (err) {
      await Promise.allSettled([
        firstValueFrom(
          this.httpService.post(`${this.SHIPPING_URL}/rollback`, {
            orderId: savedOrder.id,
          }),
        ),
        firstValueFrom(
          this.httpService.post(`${this.BILLING_URL}/rollback`, {
            orderId: savedOrder.id,
          }),
        ),
        firstValueFrom(
          this.httpService.patch(`${this.WAREHOUSE_URL}/rollback-reserve`, {
            orderId: savedOrder.id,
          }),
        ),
      ]);

      savedOrder.status = OrderStatus.CANCELLED;
      await this.orderRepo.save(savedOrder);

      throw new InternalServerErrorException(
        `Distributed Order failed: ${err}`,
      );
    }
  }
}
