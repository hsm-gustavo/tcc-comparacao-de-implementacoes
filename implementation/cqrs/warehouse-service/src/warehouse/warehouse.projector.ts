import { Controller } from '@nestjs/common';
import { EventPattern, Payload, Ctx, RmqContext } from '@nestjs/microservices';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Product } from './entities/product.entity';
import { WarehouseEvent } from './entities/warehouse-event.entity';

@Controller()
export class WarehouseProjector {
  constructor(
    @InjectRepository(Product)
    private readonly productRepository: Repository<Product>,
  ) {}

  @EventPattern('warehouse.product_created')
  async handleProductCreated(
    @Payload() event: WarehouseEvent,
    @Ctx() context: RmqContext,
  ) {
    try {
      const product = this.productRepository.create({
        id: event.aggregateId,
        name: event.data.name,
        price: event.data.price,
        stockQuantity: event.data.quantity,
      });
      await this.productRepository.save(product);
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, true);
      throw error;
    }
  }

  @EventPattern('warehouse.stock_decreased')
  async handleStockDecreased(
    @Payload() event: WarehouseEvent,
    @Ctx() context: RmqContext,
  ) {
    try {
      const product = await this.productRepository.findOneBy({
        id: event.aggregateId,
      });
      if (product) {
        product.stockQuantity -= event.data.quantity || 0;
        await this.productRepository.save(product);
      }
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, true);
      throw error;
    }
  }
}
