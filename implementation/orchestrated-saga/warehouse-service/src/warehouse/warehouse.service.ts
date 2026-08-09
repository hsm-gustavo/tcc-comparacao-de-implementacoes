import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { CreateProductDto } from './dto/create-product.dto';
import { InjectRepository } from '@nestjs/typeorm';
import { Product } from './entities/product.entity';
import { Repository } from 'typeorm';
import { ReserveStockDto } from './dto/reserve-stock.dto';
import { ReleaseStockDto } from './dto/release-stock.dto';

@Injectable()
export class WarehouseService {
  constructor(
    @InjectRepository(Product) private productsRepository: Repository<Product>,
  ) {}

  async create(createProductDto: CreateProductDto) {
    const product = this.productsRepository.create(createProductDto);
    return this.productsRepository.save(product);
  }

  async findAll() {
    return this.productsRepository.find({
      order: {
        id: 'ASC',
      },
    });
  }

  async findOne(id: string) {
    const product = await this.productsRepository.findOne({
      where: { id },
    });
    if (!product) throw new NotFoundException('Product not found');
    return product;
  }

  async reserveStock(dto: ReserveStockDto) {
    const result = await this.productsRepository
      .createQueryBuilder()
      .update()
      .set({
        stockQuantity: () => `"stockQuantity" - :quantity`,
      })
      .where('id = :productId', {
        productId: dto.productId,
      })
      .andWhere('"stockQuantity" >= :quantity', {
        quantity: dto.quantity,
      })
      .setParameters({
        quantity: dto.quantity,
      })
      .execute();

    if (result.affected === 1) {
      return {
        productId: dto.productId,
        quantity: dto.quantity,
        reserved: true,
      };
    }

    const productExists = await this.productsRepository.existsBy({
      id: dto.productId,
    });

    if (!productExists) {
      throw new NotFoundException('Product not found');
    }

    throw new BadRequestException('Insufficient stock');
  }

  async releaseStock(dto: ReleaseStockDto) {
    const product = await this.productsRepository.findOneBy({
      id: dto.productId,
    });
    if (product) {
      product.stockQuantity += dto.quantity;
      await this.productsRepository.save(product);
    }
  }

  async remove(id: string) {
    const product = await this.findOne(id);
    return this.productsRepository.remove(product);
  }
}
