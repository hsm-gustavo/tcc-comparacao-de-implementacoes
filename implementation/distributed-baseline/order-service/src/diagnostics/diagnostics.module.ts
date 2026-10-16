import { MiddlewareConsumer, Module, NestModule } from '@nestjs/common';
import type { NextFunction, Request, Response } from 'express';
import { randomUUID } from 'node:crypto';
import { diagnosticContext, diagnosticsEnabled } from './context';
import { DiagnosticsService } from './diagnostics.service';

@Module({ providers: [DiagnosticsService] })
export class DiagnosticsModule implements NestModule {
  configure(consumer: MiddlewareConsumer) {
    if (!diagnosticsEnabled()) return;
    consumer
      .apply((request: Request, _response: Response, next: NextFunction) => {
        diagnosticContext.run(
          { requestId: request.get('X-Request-Id') ?? randomUUID() },
          next,
        );
      })
      .forRoutes('orders');
  }
}
