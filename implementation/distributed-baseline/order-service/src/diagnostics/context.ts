import { AsyncLocalStorage } from 'node:async_hooks';

export type DiagnosticContext = {
  requestId?: string;
  pilotStage?: string;
  orderId?: string;
  operation?: string;
};

export const diagnosticContext = new AsyncLocalStorage<DiagnosticContext>();
export const diagnosticsEnabled = () => process.env.DB_DIAGNOSTICS === '1';

export function withDiagnosticOperation<T>(
  operation: string,
  context: Record<string, unknown>,
  callback: () => T,
): T {
  if (!diagnosticsEnabled()) return callback();
  return diagnosticContext.run(
    {
      ...diagnosticContext.getStore(),
      operation,
      ...(typeof context.orderId === 'string'
        ? { orderId: context.orderId }
        : {}),
    },
    callback,
  );
}

export function diagnosticHttpConfig() {
  if (!diagnosticsEnabled()) return undefined;
  const headers: Record<string, string> = {};
  const requestId = diagnosticContext.getStore()?.requestId;
  if (requestId) headers['X-Request-Id'] = requestId;
  if (process.env.RUN_ID) headers['X-Run-Id'] = process.env.RUN_ID;
  return { headers };
}
