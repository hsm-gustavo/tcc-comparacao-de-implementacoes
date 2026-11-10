import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';
import { HandledRmqServer } from './handled-rmq.server';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  app.enableShutdownHooks();

  app.connectMicroservice({
    strategy: new HandledRmqServer({
      urls: [process.env.RABBITMQ_URL ?? 'amqp://guest:guest@localhost:5672'],
      queue: 'warehouse-saga-queue',
      queueOptions: {
        durable: true,
      },
      noAck: false,
    }),
  });

  const swaggerConfig = new DocumentBuilder()
    .setTitle('Warehouse Service API')
    .setDescription('API documentation for the Warehouse Service')
    .setVersion('1.0')
    .build();

  const swaggerDocument = SwaggerModule.createDocument(app, swaggerConfig);
  SwaggerModule.setup('docs', app, swaggerDocument);

  await app.startAllMicroservices();
  await app.listen(process.env.PORT ?? 3001);
}
void bootstrap();
