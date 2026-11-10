import {
  GatewayTimeoutException,
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
import { firstValueFrom, Observable } from 'rxjs';
import { AxiosResponse } from 'axios';
import { AxiosError } from 'axios';
import { isAxiosError } from 'axios';

@Injectable()
export class OrderService {
  private readonly WAREHOUSE_URL =
    process.env.WAREHOUSE_URL || 'http://localhost:3002/warehouse';
  private readonly BILLING_URL =
    process.env.BILLING_URL || 'http://localhost:3003/billing';
  private readonly SHIPPING_URL =
    process.env.SHIPPING_URL || 'http://localhost:3004/shipping';
  private readonly logger = new Logger(OrderService.name);

  constructor(
    @InjectRepository(Order) private orderRepo: Repository<Order>,
    @InjectRepository(OrderItem) private orderItemRepo: Repository<OrderItem>,
    private readonly httpService: HttpService,
  ) {}
  async createOrder(dto: CreateOrderDto) {
    const startedAt = performance.now();
    let stockReserved = false;
    let paymentProcessed = false;
    let shipmentCreated = false;
    let remoteInFlight = false;

    const savedOrder = await this.orderRepo.manager.transaction(
      async (manager) => {
        const order = manager.create(Order, {
          customerId: dto.customerId,
          totalAmount: dto.amount,
        });
        const saved = await this.measure(
          'database.order.save',
          () => manager.save(Order, order),
          {
            productId: dto.productId,
            customerId: dto.customerId,
          },
        );

        const item = manager.create(OrderItem, {
          order: saved,
          productId: dto.productId,
          quantity: dto.quantity,
        });
        await this.measure(
          'database.order-item.save',
          () => manager.save(OrderItem, item),
          {
            orderId: saved.id,
            productId: dto.productId,
          },
        );

        return saved;
      },
    );

    try {
      // Step 1: reserve stock.
      remoteInFlight = true;
      await this.callService(
        'warehouse.reserve',
        this.httpService.patch(`${this.WAREHOUSE_URL}/reserve`, {
          productId: dto.productId,
          quantity: dto.quantity,
        }),
        {
          orderId: savedOrder.id,
          productId: dto.productId,
        },
      );
      remoteInFlight = false;
      stockReserved = true;

      // Step 2: process payment.
      remoteInFlight = true;
      await this.callService(
        'billing.process',
        this.httpService.post(`${this.BILLING_URL}/process`, {
          orderId: savedOrder.id,
          amount: dto.amount,
        }),
        {
          orderId: savedOrder.id,
          productId: dto.productId,
        },
      );
      remoteInFlight = false;
      paymentProcessed = true;

      // Step 3: create shipment.
      remoteInFlight = true;
      await this.callService(
        'shipping.create',
        this.httpService.post(this.SHIPPING_URL, {
          orderId: savedOrder.id,
        }),
        {
          orderId: savedOrder.id,
          productId: dto.productId,
        },
      );
      remoteInFlight = false;
      shipmentCreated = true;

      savedOrder.status = OrderStatus.CONFIRMED;

      const confirmedOrder = await this.measure(
        'database.order.confirm',
        () => this.orderRepo.save(savedOrder),
        {
          orderId: savedOrder.id,
        },
      );
      const tt = this.elapsed(startedAt);
      if (tt >= 8000) {
        this.logger.log({
          event: 'orchestrated-saga.completed',
          orderId: savedOrder.id,
          productId: dto.productId,
          durationMs: tt,
        });
      }

      return confirmedOrder;
    } catch (error: unknown) {
      const compensated = await this.compensate(
        dto,
        savedOrder.id,
        shipmentCreated,
        paymentProcessed,
        stockReserved,
      );

      const outcomeUnknown =
        remoteInFlight &&
        (!isAxiosError(error) ||
          !error.response ||
          error.response.status >= 500 ||
          [408, 429].includes(error.response.status));
      if (compensated && !outcomeUnknown) {
        savedOrder.status = OrderStatus.CANCELLED;
        await this.measure(
          'database.order.cancel',
          () => this.orderRepo.save(savedOrder),
          {
            orderId: savedOrder.id,
          },
        );
      } else {
        this.logger.error({
          event: 'orchestrated-saga.unresolved',
          orderId: savedOrder.id,
          outcomeUnknown,
          compensationComplete: compensated,
        });
      }

      const details = this.extractErrorDetails(error);

      this.logger.error({
        event: 'orchestrated-saga.failed',
        orderId: savedOrder.id,
        productId: dto.productId,
        durationMs: this.elapsed(startedAt),
        ...details,
      });

      if (
        isAxiosError(error) &&
        (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT')
      ) {
        throw new GatewayTimeoutException(
          `Orchestrated saga timed out during ${details.operation ?? 'an internal call'}`,
        );
      }

      throw new InternalServerErrorException(
        `Orchestrated saga failed: ${details.message}`,
      );
    }
  }

  private async compensate(
    dto: CreateOrderDto,
    orderId: string,
    shipmentCreated: boolean,
    paymentProcessed: boolean,
    stockReserved: boolean,
  ) {
    const compensationSteps: Array<() => Promise<unknown>> = [];

    if (shipmentCreated) {
      compensationSteps.push(() =>
        this.callService(
          'shipping.cancel',
          this.httpService.post(`${this.SHIPPING_URL}/cancel`, { orderId }),
          { orderId, productId: dto.productId },
        ),
      );
    }

    if (paymentProcessed) {
      compensationSteps.push(() =>
        this.callService(
          'billing.refund',
          this.httpService.post(`${this.BILLING_URL}/refund`, { orderId }),
          { orderId, productId: dto.productId },
        ),
      );
    }

    if (stockReserved) {
      compensationSteps.push(() =>
        this.callService(
          'warehouse.release',
          this.httpService.patch(`${this.WAREHOUSE_URL}/release`, {
            productId: dto.productId,
            quantity: dto.quantity,
          }),
          { orderId, productId: dto.productId },
        ),
      );
    }

    let failedCompensations = 0;
    for (const compensate of compensationSteps) {
      try {
        await compensate();
      } catch {
        failedCompensations++;
      }
    }

    if (failedCompensations > 0) {
      this.logger.error({
        event: 'orchestrated-saga.compensation-failed',
        orderId,
        failedCompensations,
      });
    }
    return failedCompensations === 0;
  }

  private async callService<T>(
    operation: string,
    request$: Observable<AxiosResponse<T>>,
    context: Record<string, unknown> = {},
  ): Promise<T> {
    const startedAt = performance.now();

    try {
      const response = await firstValueFrom(request$);
      const tt = this.elapsed(startedAt);
      if (tt >= 8000) {
        this.logger.log({
          event: 'internal-http.completed',
          operation,
          durationMs: tt,
          statusCode: response.status,
          ...context,
        });
      }

      return response.data;
    } catch (err: unknown) {
      const details = this.extractErrorDetails(err);

      this.logger.error({
        event: 'internal-http.failed',
        durationMs: this.elapsed(startedAt),
        ...context,
        ...details,
        operation,
      });

      if (isAxiosError(err)) {
        (
          err as AxiosError & {
            operation?: string;
          }
        ).operation = operation;
      }

      throw err;
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

      const tt = this.elapsed(startedAt);
      if (tt >= 8000) {
        this.logger.log({
          event: 'operation.completed',
          operation,
          durationMs: tt,
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
    if (isAxiosError(error)) {
      const axiosError = error as AxiosError & {
        operation?: string;
      };

      return {
        operation: axiosError.operation,
        type: 'axios',
        message: this.extractResponseMessage(axiosError) ?? axiosError.message,
        code: axiosError.code,
        statusCode: axiosError.response?.status,
        method: axiosError.config?.method?.toUpperCase(),
        url: axiosError.config?.url,
      };
    }

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

  private extractResponseMessage(error: AxiosError): string | undefined {
    const data = error.response?.data;

    if (typeof data === 'object' && data !== null && 'message' in data) {
      const message = data.message;

      if (typeof message === 'string') {
        return message;
      }

      if (Array.isArray(message)) {
        return message.join(', ');
      }
    }

    if (typeof data === 'string') {
      return data;
    }

    return undefined;
  }

  private elapsed(startedAt: number): number {
    return Number((performance.now() - startedAt).toFixed(2));
  }
}
