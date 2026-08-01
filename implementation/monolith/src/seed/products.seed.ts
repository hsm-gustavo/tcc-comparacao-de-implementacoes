import { NestFactory } from '@nestjs/core';
import { DataSource } from 'typeorm';
import { AppModule } from 'src/app.module';
import { Product } from 'src/warehouse/entities/product.entity';

const productsToSeed: Array<Pick<Product, 'name' | 'price' | 'stockQuantity'>> =
  [
    { name: 'Notebook Gamer Nitro 16', price: 6499.9, stockQuantity: 12 },
    { name: 'Mouse Sem Fio MX Master', price: 459.9, stockQuantity: 30 },
    { name: 'Teclado Mecânico RGB', price: 329.9, stockQuantity: 24 },
    { name: 'Monitor 27 Polegadas 144Hz', price: 1399.9, stockQuantity: 18 },
    { name: 'Headset Bluetooth Pro', price: 599.9, stockQuantity: 20 },
  ];

async function runSeed() {
  const app = await NestFactory.createApplicationContext(AppModule);

  try {
    const dataSource = app.get(DataSource);
    const productRepository = dataSource.getRepository(Product);

    for (const productData of productsToSeed) {
      const existingProduct = await productRepository.findOneBy({
        name: productData.name,
      });

      if (existingProduct) {
        existingProduct.price = productData.price;
        existingProduct.stockQuantity = productData.stockQuantity;
        await productRepository.save(existingProduct);
        continue;
      }

      const product = productRepository.create(productData);
      await productRepository.save(product);
    }

    console.log('Seed de produtos executado com sucesso.');
  } catch (error) {
    console.error('Falha ao executar seed de produtos:', error);
    process.exitCode = 1;
  } finally {
    await app.close();
  }
}

void runSeed();
