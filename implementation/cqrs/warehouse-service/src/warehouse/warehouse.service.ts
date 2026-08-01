import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { CreateProductDto } from './dto/create-product.dto';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { WarehouseEvent } from './entities/warehouse-event.entity';
import {
  ClientProxy,
  ClientProxyFactory,
  Transport,
} from '@nestjs/microservices';
import { Product } from './entities/product.entity';

@Injectable()
export class WarehouseService {
  private readonly rabbitUrl =
    process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672';
  private readonly billingQueue = 'billing-service-queue';

  constructor(
    @InjectRepository(WarehouseEvent)
    private readonly eventRepository: Repository<WarehouseEvent>,
    @InjectRepository(Product)
    private readonly productRepository: Repository<Product>,
  ) {}

  private createClient(queue: string): ClientProxy {
    return ClientProxyFactory.create({
      transport: Transport.RMQ,
      options: {
        urls: [this.rabbitUrl],
        queue,
        queueOptions: { durable: true },
      },
    });
  }

  private async emit(
    queue: string,
    pattern: string,
    payload: Record<string, unknown>,
  ) {
    const client = this.createClient(queue);

    try {
      await new Promise<void>((resolve, reject) => {
        client.emit(pattern, payload).subscribe({
          complete: resolve,
          error: reject,
        });
      });
    } finally {
      await client.close();
    }
  }

  async create(createProductDto: CreateProductDto) {
    const productId = crypto.randomUUID();

    const event = this.eventRepository.create({
      aggregateId: productId,
      type: 'ProductCreated',
      data: {
        name: createProductDto.name,
        price: createProductDto.price,
        quantity: createProductDto.stockQuantity,
      },
    });

    await this.eventRepository.save(event);
    return { id: productId, ...createProductDto };
  }

  async reserveStock(
    productId: string,
    quantityToReserve: number,
    orderId: string,
  ) {
    const productState = await this.rebuildProductState(productId);

    if (!productState.exists) throw new NotFoundException('Product not found');

    if (productState.stockQuantity < quantityToReserve) {
      const failedEvent = this.eventRepository.create({
        aggregateId: productId,
        type: 'StockDeductionFailed',
        data: {
          orderId,
          quantity: quantityToReserve,
          reason: 'Insufficient stock',
        },
      });
      await this.eventRepository.save(failedEvent);

      throw new BadRequestException('Insufficient stock');
    }

    const successEvent = this.eventRepository.create({
      aggregateId: productId,
      type: 'StockDecreased',
      data: {
        orderId,
        quantity: quantityToReserve,
      },
    });
    await this.eventRepository.save(successEvent);

    const totalAmount = productState.price * quantityToReserve;

    if (orderId !== 'MANUAL_HTTP') {
      await this.emit(this.billingQueue, 'warehouse.stock_reserved', {
        orderId,
        productId,
        quantity: quantityToReserve,
        totalAmount,
      });
    }

    return { orderId, productId, status: 'SUCCESS' };
  }

  async findOne(productId: string) {
    const state = await this.rebuildProductState(productId);
    if (!state.exists) throw new NotFoundException('Product not found');
    return state;
  }

  private async rebuildProductState(productId: string) {
    const events = await this.eventRepository.find({
      where: { aggregateId: productId },
      order: { createdAt: 'ASC' },
    });

    if (events.length === 0) {
      return {
        exists: false,
        id: productId,
        name: '',
        price: 0,
        stockQuantity: 0,
      };
    }

    let name = '';
    let price = 0;
    let stockQuantity = 0;

    for (const event of events) {
      switch (event.type) {
        case 'ProductCreated':
          name = event.data.name || '';
          price = event.data.price || 0;
          stockQuantity += event.data.quantity || 0;
          break;
        case 'StockDecreased':
          stockQuantity -= event.data.quantity || 0;
          break;
        case 'StockReleased':
          stockQuantity += event.data.quantity || 0;
          break;
      }
    }

    return {
      exists: true,
      id: productId,
      name,
      price,
      stockQuantity,
    };
  }

  async replayEvents() {
    const events = await this.eventRepository.find({
      order: { createdAt: 'ASC' },
    });

    let processedCount = 0;

    for (const event of events) {
      const productId = event.aggregateId;

      if (event.type === 'ProductCreated') {
        let product = await this.productRepository.findOneBy({ id: productId });

        if (!product) {
          product = this.productRepository.create({
            id: productId,
            name: event.data.name,
            price: event.data.price,
            stockQuantity: event.data.quantity,
          });
        } else {
          product.name = event.data.name || product.name;
          product.price = event.data.price || product.price;
          product.stockQuantity =
            event.data.quantity !== undefined
              ? event.data.quantity
              : product.stockQuantity;
        }
        await this.productRepository.save(product);
        processedCount++;
      }

      if (event.type === 'StockDecreased') {
        const product = await this.productRepository.findOneBy({
          id: productId,
        });
        if (product) {
          product.stockQuantity -= event.data.quantity || 0;
          await this.productRepository.save(product);
          processedCount++;
        }
      }

      if (event.type === 'StockReleased') {
        const product = await this.productRepository.findOneBy({
          id: productId,
        });
        if (product) {
          product.stockQuantity += event.data.quantity || 0;
          await this.productRepository.save(product);
          processedCount++;
        }
      }
    }

    return {
      success: true,
      message: `Replay finalizado. ${processedCount} eventos de estoque processados.`,
    };
  }
}
