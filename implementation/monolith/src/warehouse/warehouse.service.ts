import { BadRequestException, Injectable } from '@nestjs/common';
import { CreateProductDto } from './dto/create-product.dto';
import { InjectRepository } from '@nestjs/typeorm';
import { Product } from './entities/product.entity';
import { EntityManager, Repository } from 'typeorm';

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
      relations: ['orderItems'],
    });
    if (!product) throw new BadRequestException('Product not found');
    return product;
  }

  async reserveStock(
    productId: string,
    quantity: number,
    manager?: EntityManager,
  ) {
    const repo = manager
      ? manager.getRepository(Product)
      : this.productsRepository;

    const product = await repo.findOneBy({ id: productId });

    if (!product) throw new BadRequestException('Product not found');

    if (product.stockQuantity < quantity) {
      throw new BadRequestException('Insufficient stock');
    }

    product.stockQuantity -= quantity;
    return repo.save(product);
  }

  async releaseStock(
    productId: string,
    quantity: number,
    manager?: EntityManager,
  ) {
    const repo = manager
      ? manager.getRepository(Product)
      : this.productsRepository;

    const product = await repo.findOneBy({ id: productId });
    if (product) {
      product.stockQuantity += quantity;
      await repo.save(product);
    }
  }

  async remove(id: string) {
    const product = await this.findOne(id);
    return this.productsRepository.remove(product);
  }
}
