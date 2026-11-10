import type { ReadPacket } from '@nestjs/microservices';
import { BadRequestException } from '@nestjs/common';
import { MessageRecovery } from './message-recovery';
import { RmqContext, ServerRMQ } from '@nestjs/microservices';
import type { EventEmitter } from 'node:events';

type ChannelManager = {
  createChannel(options: unknown): EventEmitter;
};

/** Capture consumer channel setup errors without a global exception handler. */
export class HandledRmqServer extends ServerRMQ {
  private readonly recovery = new MessageRecovery();

  override async handleEvent(
    pattern: string,
    packet: ReadPacket,
    context: RmqContext,
  ) {
    if (!this.getHandlerByPattern(pattern)) {
      await this.recovery.handle(
        context,
        new BadRequestException('Unknown event pattern'),
        this.queue,
      );
      return;
    }
    return super.handleEvent(pattern, packet, context) as Promise<unknown>;
  }

  override async close() {
    try {
      await super.close();
    } finally {
      await this.recovery.onModuleDestroy();
    }
  }

  override createClient<T = ChannelManager>(): T {
    const manager = super.createClient<ChannelManager>();
    const createChannel = manager.createChannel.bind(
      manager,
    ) as ChannelManager['createChannel'];
    manager.createChannel = (options) => {
      const channel = createChannel(options);
      channel.on('error', (error: Error) => {
        this.logger.error(error);
      });
      return channel;
    };
    return manager as unknown as T;
  }
}
