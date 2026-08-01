import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';

import { Outbox } from './entities/outbox.entity';
import { OutboxService } from './outbox.service';
import { OutboxProcessorService } from './outbox-processor.service';

@Module({
  imports: [TypeOrmModule.forFeature([Outbox])],
  providers: [OutboxService, OutboxProcessorService],
  exports: [OutboxService],
})
export class OutboxModule {}
