// Test-only preload: not referenced by production Dockerfiles or normal runners.
const path = require('node:path');
const { createRequire } = require('node:module');

function install({ prototype, method, mode, durationMs, amount, emit,
  now = () => performance.now(), schedule = setTimeout, unavailable }) {
  const original = prototype[method];
  if (typeof original !== 'function') throw new Error(`Missing payment boundary: ${method}`);
  let target, deadline;
  prototype[method] = async function (...args) {
    const dto = args[0];
    if (Number(dto?.amount) !== amount) return Reflect.apply(original, this, args);
    if (target === undefined) {
      if (!dto?.orderId) throw new Error('Target payment lacks orderId');
      target = dto.orderId;
      deadline = now() + durationMs;
      emit({ event: 'boundary_reached', orderId: target, method, mode });
      if (mode === 'transient') {
        emit({ event: 'fault_started', orderId: target, durationMs });
        schedule(() => emit({ event: 'fault_restored', orderId: target }), durationMs).unref?.();
      }
    }
    if (dto.orderId === target && mode === 'transient' && now() < deadline) {
      emit({ event: 'payment_rejected', orderId: target });
      throw unavailable();
    }
    emit({ event: 'payment_allowed', orderId: dto.orderId });
    return Reflect.apply(original, this, args);
  };
}

if (process.env.PAYMENT_FAULT_TEST === '1') {
  const mode = process.env.PAYMENT_FAULT_MODE;
  const architecture = process.env.ARCHITECTURE;
  const methods = { 'distributed-baseline': 'processPayment', tcc: 'preparePayment',
    'two-phase-commit': 'preparePayment', 'orchestrated-saga': 'processPayment',
    'choreographed-saga': 'processSagaPayment' };
  const durationMs = Number(process.env.PAYMENT_FAULT_MS);
  if (!methods[architecture] || !['control', 'transient'].includes(mode) ||
      !Number.isInteger(durationMs) || durationMs <= 0) throw new Error('Invalid fault-test configuration');
  const appRequire = createRequire(path.join(process.cwd(), 'package.json'));
  appRequire('reflect-metadata');
  const { BillingService } = appRequire('./dist/billing/billing.service.js');
  const { ServiceUnavailableException } = appRequire('@nestjs/common');
  install({ prototype: BillingService.prototype, method: methods[architecture], mode, durationMs,
    amount: 10, unavailable: () => new ServiceUnavailableException('Experimental payment unavailability'),
    emit: (event) => process.stdout.write(JSON.stringify({ type: 'payment_fault_test',
      timestamp: new Date().toISOString(), runId: process.env.RUN_ID, ...event }) + '\n') });
}

module.exports = { install };
