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
  private readonly WAREHOUSE_URL =
    process.env.WAREHOUSE_URL || 'http://localhost:3002/warehouse';
  private readonly BILLING_URL =
    process.env.BILLING_URL || 'http://localhost:3003/billing';
  private readonly SHIPPING_URL =
    process.env.SHIPPING_URL || 'http://localhost:3004/shipping';

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
      //2. Reserve stock
      await firstValueFrom(
        this.httpService.patch(`${this.WAREHOUSE_URL}/reserve`, {
          productId: dto.productId,
          quantity: dto.quantity,
        }),
      );

      //3. Process payment
      // critical step: if payment fails, the catch runs but the stock is already deducted
      await firstValueFrom(
        this.httpService.post(`${this.BILLING_URL}/process`, {
          orderId: savedOrder.id,
          amount: dto.amount,
        }),
      );

      //4. Create shipment
      await firstValueFrom(
        this.httpService.post(`${this.SHIPPING_URL}`, {
          orderId: savedOrder.id,
        }),
      );

      savedOrder.status = OrderStatus.CONFIRMED;
      return await this.orderRepo.save(savedOrder);
    } catch (err) {
      savedOrder.status = OrderStatus.CANCELLED;
      await this.orderRepo.save(savedOrder);

      const detailMsg =
        err.response?.data?.message || err.cause?.message || err.message;

      throw new InternalServerErrorException(
        `Distributed Order failed: ${detailMsg}`,
      );
    }
  }
}
