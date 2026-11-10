const { test } = require('node:test');
const assert = require('node:assert/strict');
delete process.env.PAYMENT_FAULT_TEST;
const { install } = require('../../environment/faults/register-payment-fault.cjs');

function fixture(mode) {
  let clock = 0, calls = 0, timer;
  const events = [];
  const prototype = { async process(dto) { calls++; return { id: dto.orderId, instance: this }; },
    async rollback() { return 'unchanged'; } };
  install({ prototype, method: 'process', mode, durationMs: 5000, amount: 10,
    now: () => clock, schedule: fn => { timer = fn; return {}; },
    unavailable: () => new Error('503 simulated'), emit: event => events.push(event) });
  return { prototype, events, calls: () => calls, restore() { clock = 5000; timer(); } };
}

test('fault rejects before any original mutation; repeated attempts reject until restoration', async () => {
  const f = fixture('transient');
  for (let i = 0; i < 2; i++) await assert.rejects(f.prototype.process({ orderId: 'target', amount: 10 }), /503/);
  assert.equal(f.calls(), 0);
  assert.equal(await f.prototype.rollback(), 'unchanged');
  f.restore();
  const result = await f.prototype.process({ orderId: 'target', amount: 10 });
  assert.equal(result.id, 'target');assert.equal(result.instance, f.prototype);assert.equal(f.calls(), 1);
  assert.equal(f.events.filter(e => e.event === 'fault_started').length, 1);
  assert.equal(f.events.filter(e => e.event === 'fault_restored').length, 1);
});

test('warmup is unaffected and cannot arm fault', async () => {
  const f = fixture('transient');
  await f.prototype.process({ orderId: 'warmup', amount: 1 });
  assert.equal(f.calls(), 1);assert.equal(f.events.length, 0);
  await assert.rejects(f.prototype.process({ orderId: 'target', amount: 10 }));
  assert.equal(f.calls(), 1);
});

test('control follows original behavior and records reached boundary without fault', async () => {
  const f = fixture('control');
  await f.prototype.process({ orderId: 'target', amount: 10 });
  assert.equal(f.calls(), 1);
  assert.deepEqual(f.events.map(e => e.event), ['boundary_reached', 'payment_allowed']);
});

test('wrong compiled-method configuration fails visibly', () => {
  assert.throws(() => install({ prototype: {}, method: 'missing' }), /Missing payment boundary/);
});
