import type { EventEmitter } from 'node:events';
import { ClientRMQ } from '@nestjs/microservices';

/** Handle ChannelWrapper errors as operation failures, not process crashes. */
export class HandledRmqClient extends ClientRMQ {
  private connecting?: Promise<unknown>;

  override connect(): Promise<unknown> {
    if (!this.connecting) {
      this.connecting = super
        .connect()
        .catch(async (error: unknown) => {
          // A failed initial setup must not poison every future publication.
          await this.close();
          throw error;
        })
        .finally(() => {
          this.connecting = undefined;
        });
    }
    return this.connecting;
  }

  async publishRaw(
    content: Buffer,
    properties: Record<string, unknown>,
  ): Promise<void> {
    await this.connect();
    const channel = this.channel as {
      sendToQueue(
        queue: string,
        content: Buffer,
        options: Record<string, unknown>,
      ): Promise<void>;
    };
    await channel.sendToQueue(this.queue, content, properties);
  }

  override createChannel(): Promise<void> {
    const ready = new Promise<void>((resolve, reject) => {
      // Nest creates the wrapper synchronously; its setup completes asynchronously.
      super.createChannel().then(resolve, reject);
      (this.channel as EventEmitter).on('error', (error: Error) => {
        this.logger.error(error);
        reject(error);
      });
    });
    // Nest can start an initial channel before replacing its connection promise.
    // Mark that promise handled, while preserving rejection for actual awaiters.
    void ready.catch(() => undefined);
    return ready;
  }
}
