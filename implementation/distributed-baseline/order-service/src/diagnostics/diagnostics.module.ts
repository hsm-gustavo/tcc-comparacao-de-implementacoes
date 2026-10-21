import { MiddlewareConsumer, Module, NestModule } from '@nestjs/common';
import type { NextFunction, Request, Response } from 'express';
import { randomUUID } from 'node:crypto';
import { diagnosticContext, diagnosticsEnabled } from './context';
import { DiagnosticsService } from './diagnostics.service';

@Module({ providers: [DiagnosticsService] })
export class DiagnosticsModule implements NestModule {
  constructor(private readonly diagnostics: DiagnosticsService) {}

  configure(consumer: MiddlewareConsumer) {
    if (!diagnosticsEnabled()) return;
    consumer
      .apply((request: Request, _response: Response, next: NextFunction) => {
        diagnosticContext.run(
          {
            requestId: request.get('X-Request-Id') ?? randomUUID(),
            pilotStage: this.diagnostics.recordPilotRequest(
              request.get('X-Pilot-Stage'),
            ),
          },
          next,
        );
      })
      .forRoutes('orders');
  }
}
