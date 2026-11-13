const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const req = createRequire(root + '/implementation/distributed-baseline/order-service/package.json');
const ts = req('typescript');
const Module = require('node:module');
const originalLoad = Module._load;
// Load the actual TS services without installing dependencies in other apps.
// Only RabbitMQ publication is replaced in these unit-level checks (no broker).
function loadWithSharedDependencies(name, parent, ...args) {
  if (name === '../saga-publisher') return { SagaPublisher: class {} };
  if (name.startsWith('.') || name.startsWith('/') || name.startsWith('node:')) return originalLoad.call(this, name, parent, ...args);
  try { return originalLoad.call(this, name, parent, ...args); }
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    return originalLoad.call(this, req.resolve(name), parent, ...args);
  }
}
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023,
    experimentalDecorators: true, emitDecoratorMetadata: true, esModuleInterop: true },
}).outputText, filename);
req('reflect-metadata');
const { DataSource } = req('typeorm');
const { MemoryDatabase } = require('./memory-database.cjs');
const architectures = ['distributed-baseline', 'tcc', 'two-phase-commit', 'orchestrated-saga', 'choreographed-saga'];
async function setup(arch, service) {
  Module._load = loadWithSharedDependencies;
  const base = `${root}/implementation/${arch}/${service}-service/src/${service}`;
  const entities = fs.readdirSync(base + '/entities').filter(f => f.endsWith('.entity.ts'))
    .flatMap(f => Object.values(require(base + '/entities/' + f)).filter(v => typeof v === 'function'));
  const Service = Object.values(require(base + '/' + service + '.service.ts')).find(v => typeof v === 'function');
  Module._load = originalLoad;
  const source = new DataSource({ type: 'postgres', entities });
  source.driver.connect = () => { throw new Error('TEST: PostgreSQL connection forbidden'); };
  await source.buildMetadatas();
  const db = new MemoryDatabase(source);
  const byName = Object.fromEntries(entities.map(e => [e.name, e]));
  return { db, byName, Service };
}
for (const arch of architectures) {
  test(arch + ': stock conservation under concurrency, rollback, relations and terminal state', async () => {
    const { db, byName: { Product, StockReservation }, Service } = await setup(arch, 'warehouse');
    const repo = db.repository(Product);
    const service = new Service(repo, StockReservation ? db.repository(StockReservation) : undefined);
    const product = await service.create({ name: 'fixture', price: 1, stockQuantity: 10 });
    if (arch === 'tcc' || arch === 'two-phase-commit') {
      const results = await Promise.allSettled(Array.from({ length: 20 }, (_, i) => service.prepareStockReservation({ productId: product.id, orderId: 'order-' + i, quantity: 1 })));
      assert.equal(results.filter(r => r.status === 'fulfilled').length, 10);
      await Promise.all(Array.from({ length: 10 }, (_, i) => service.commitStockReservation({ orderId: 'order-' + i })));
    } else if (arch === 'choreographed-saga') {
      const payload = { orderId: 'same-order', productId: product.id, quantity: 10 };
      await Promise.all(Array.from({ length: 10 }, () => service.reserveForSaga(payload)));
      assert.equal((await db.repository(StockReservation).find()).length, 1);
    } else {
      const results = await Promise.allSettled(Array.from({ length: 20 }, () => service.reserveStock({ productId: product.id, quantity: 1 })));
      assert.equal(results.filter(r => r.status === 'fulfilled').length, 10);
    }
    assert.equal((await service.findOne(product.id)).stockQuantity, 0);
    const error = new Error('rollback sentinel');
    await assert.rejects(db.manager.transaction(async m => {
      const p = await m.findOneBy(Product, { id: product.id });
      p.stockQuantity = 99;
      await m.save(Product, p);
      throw error;
    }), e => e === error);
    assert.equal((await service.findOne(product.id)).stockQuantity, 0);
    await assert.rejects(db.manager.query('SELECT 1'), /unsupported SQL/);
  });
  for (const kind of ['billing', 'shipping']) test(arch + ': ' + kind + ' real entities/services', async () => {
    const { db, byName, Service } = await setup(arch, kind);
    const entity = byName.Payment || byName.Shipment;
    const repository = db.repository(entity);
    const service = new Service(repository);
    const dto = { orderId: 'fixture', amount: 10 };
    if (arch === 'tcc' || arch === 'two-phase-commit') {
      if (kind === 'billing') {
        await service.preparePayment(dto); await service.commitPayment(dto.orderId);
        await assert.rejects(service.rollbackPayment(dto.orderId));
      } else {
        await service.prepareShipment(dto); await service.commitShipment(dto.orderId);
        await assert.rejects(service.rollbackShipment(dto.orderId));
      }
    } else if (arch === 'choreographed-saga') {
      if (kind === 'billing') await service.processSagaPayment(dto);
      else await service.createSagaShipment(dto);
    } else {
      if (kind === 'billing') await service.processPayment(dto);
      else await service.createShipment(dto);
    }
    assert.equal((await repository.findOneBy({ orderId: dto.orderId })).orderId, dto.orderId);
  });
  test(arch + ': order/items atomicity, defaults, relation and conditional terminal transitions', async () => {
    const { db, byName: { Order, OrderItem } } = await setup(arch, 'order');
    let saved;
    await db.manager.transaction(async m => {
      saved = await m.save(Order, m.create(Order, { customerId: 'fixture', totalAmount: 10 }));
      await m.save(OrderItem, m.create(OrderItem, { order: saved, productId: 'fixture', quantity: 1 }));
    });
    const repo = db.repository(Order);
    assert.equal(saved.status, 'pending');
    assert.equal((await repo.findOne({ where: { id: saved.id }, relations: ['items'] })).items.length, 1);
    assert.equal((await repo.update({ id: saved.id, status: 'pending' }, { status: 'confirmed' })).affected, 1);
    assert.equal((await repo.update({ id: saved.id, status: 'pending' }, { status: 'cancelled' })).affected, 0);
    await assert.rejects(db.manager.transaction(async m => {
      await m.save(Order, m.create(Order, { customerId: 'rolledback', totalAmount: 1 }));
      throw new Error('expected rollback');
    }));
    assert.equal((await repo.find()).length, 1);
  });
}
