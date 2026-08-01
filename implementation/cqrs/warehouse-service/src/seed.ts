import 'reflect-metadata';
import { DataSource } from 'typeorm';
import { Product } from './warehouse/entities/product.entity';

const warehouseDataSource = new DataSource({
  type: 'postgres',
  host: 'localhost',
  port: 5433,
  username: 'user',
  password: 'password',
  database: 'warehouse',
  entities: [Product],
  synchronize: true,
});

async function seedProducts() {
  await warehouseDataSource.initialize();

  const productRepository = warehouseDataSource.getRepository(Product);

  const products: Array<Pick<Product, 'name' | 'price' | 'stockQuantity'>> = [
    { name: 'Teclado Mecânico', price: 299.9, stockQuantity: 12 },
    { name: 'Mouse Sem Fio', price: 149.5, stockQuantity: 30 },
    { name: 'Monitor 24 Polegadas', price: 899.0, stockQuantity: 8 },
    { name: 'Headset Gamer', price: 249.9, stockQuantity: 20 },
    { name: 'Webcam Full HD', price: 189.9, stockQuantity: 15 },
  ];

  await productRepository.clear();
  await productRepository.save(products);

  console.log('Seed concluído: 5 produtos inseridos com sucesso.');

  await warehouseDataSource.destroy();
}

seedProducts().catch(async (error: unknown) => {
  console.error('Erro ao executar seed de produtos:', error);

  if (warehouseDataSource.isInitialized) {
    await warehouseDataSource.destroy();
  }

  process.exit(1);
});
