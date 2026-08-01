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
    return this.productsRepository.find();
  }

  async findOne(id: string) {
    const product = await this.productsRepository.findOne({
      where: { id },
    });
    if (!product) throw new NotFoundException('Product not found');
    return product;
  }

  async reserveStock(dto: ReserveStockDto) {
    const product = await this.productsRepository.findOneBy({
      id: dto.productId,
    });

    if (!product) throw new NotFoundException('Product not found');

    if (product.stockQuantity < dto.quantity) {
      throw new BadRequestException('Insufficient stock');
    }

    product.stockQuantity -= dto.quantity;
    return this.productsRepository.save(product);
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
