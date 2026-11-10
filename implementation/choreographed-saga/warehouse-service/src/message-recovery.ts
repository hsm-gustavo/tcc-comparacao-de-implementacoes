import { HttpException, Injectable, Logger } from '@nestjs/common';
import { RmqContext } from '@nestjs/microservices';
import { SagaPublisher } from './saga-publisher';

type Delivery = {
  content: Buffer;
  properties: { headers?: Record<string, unknown>; [key: string]: unknown };
};
type Acknowledgements = {
  ack(message: Delivery): void;
  nack(message: Delivery, multiple: boolean, requeue: boolean): void;
};

/** Retain the original until the broker confirms its replacement. No silent discard. */
@Injectable()
export class MessageRecovery extends SagaPublisher {
  private readonly logger = new Logger(MessageRecovery.name);

  async handle(context: RmqContext, error: unknown, queue: string) {
    const message = context.getMessage() as Delivery;
    const channel = context.getChannelRef() as Acknowledgements;
    const headers = message.properties.headers ?? {};
    const rawAttempt = Number(headers['x-saga-retry'] ?? 0);
    const attempt =
      Number.isInteger(rawAttempt) && rawAttempt >= 0 ? rawAttempt : 3;
    const permanent =
      error instanceof HttpException && [400, 404].includes(error.getStatus());
    const delays = [1000, 5000, 30000];
    const retry = !permanent && attempt < delays.length;
    const destination = retry
      ? `${queue}.retry.${attempt + 1}`
      : `${queue}.errors`;
    const options = retry
      ? {
          durable: true,
          arguments: {
            'x-message-ttl': delays[attempt],
            'x-dead-letter-exchange': '',
            'x-dead-letter-routing-key': queue,
          },
        }
      : { durable: true };
    try {
      await this.client(destination, options).publishRaw(message.content, {
        ...message.properties,
        expiration: undefined,
        persistent: true,
        headers: {
          ...headers,
          'x-saga-retry': retry ? attempt + 1 : attempt,
          'x-saga-source-queue': queue,
          'x-saga-error': String(error).slice(0, 500),
        },
      });
    } catch (publishError) {
      // Publication could not be confirmed: leave the delivery recoverable.
      this.logger.error(`Recovery publication failed: ${String(publishError)}`);
      channel.nack(message, false, true);
      return;
    }
    channel.ack(message);
    this.logger.warn(
      JSON.stringify({
        type: 'saga_message_recovery',
        queue,
        destination,
        attempt,
        action: retry ? 'retry' : 'park',
        error: String(error),
      }),
    );
  }
}
