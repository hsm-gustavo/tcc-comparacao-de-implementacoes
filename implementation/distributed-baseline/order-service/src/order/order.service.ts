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
import {
  diagnosticHttpConfig,
  withDiagnosticOperation,
} from '../diagnostics/context';

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
      //2. Reserve stock
      await this.callService(
        'warehouse.reserve',
        this.httpService.patch(
          `${this.WAREHOUSE_URL}/reserve`,
          {
            productId: dto.productId,
            quantity: dto.quantity,
          },
          diagnosticHttpConfig(),
        ),
        {
          orderId: savedOrder.id,
          productId: dto.productId,
        },
      );

      //3. Process payment
      // critical step: if payment fails, the catch runs but the stock is already deducted
      await this.callService(
        'billing.process',
        this.httpService.post(
          `${this.BILLING_URL}/process`,
          {
            orderId: savedOrder.id,
            amount: dto.amount,
          },
          diagnosticHttpConfig(),
        ),
        {
          orderId: savedOrder.id,
          productId: dto.productId,
        },
      );

      //4. Create shipment
      await this.callService(
        'shipping.create',
        this.httpService.post(
          this.SHIPPING_URL,
          {
            orderId: savedOrder.id,
          },
          diagnosticHttpConfig(),
        ),
        {
          orderId: savedOrder.id,
          productId: dto.productId,
        },
      );

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
          event: 'distributed-order.completed',
          orderId: savedOrder.id,
          productId: dto.productId,
          durationMs: tt,
        });
      }

      return confirmedOrder;
    } catch (error: unknown) {
      savedOrder.status = OrderStatus.CANCELLED;
      await this.measure(
        'database.order.cancel',
        () => this.orderRepo.save(savedOrder),
        {
          orderId: savedOrder.id,
        },
      );

      const details = this.extractErrorDetails(error);

      this.logger.error({
        event: 'distributed-order.failed',
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
          `Distributed Order timed out during ${details.operation ?? 'an internal call'}`,
        );
      }

      throw new InternalServerErrorException(
        `Distributed Order failed: ${details.message}`,
      );
    }
  }

  private async callService<T>(
    operation: string,
    request$: Observable<AxiosResponse<T>>,
    context: Record<string, unknown> = {},
  ): Promise<T> {
    const startedAt = performance.now();

    try {
      const response = await withDiagnosticOperation(operation, context, () =>
        firstValueFrom(request$),
      );
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
      const result = await withDiagnosticOperation(
        operation,
        context,
        callback,
      );

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
