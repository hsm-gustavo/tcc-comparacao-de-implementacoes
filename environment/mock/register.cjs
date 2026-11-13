// Must be preloaded before Nest creates any PostgreSQL DataSource.
if (process.env.DB_MODE === 'mock') {
  if (process.env.DB_DIAGNOSTICS === '1') throw new Error('DB_MODE=mock requires DB_DIAGNOSTICS=0: no real PostgreSQL pool exists');
  const { createRequire } = require('node:module');
  const appRequire = createRequire(process.cwd() + '/package.json');
  const { DataSource } = appRequire('typeorm');
  const { MemoryDatabase } = require('./memory-database.cjs');
  DataSource.prototype.initialize = async function () {
    if (this.isInitialized) throw new Error('Mock database already initialized');
    await this.buildMetadatas();
    const memory = new MemoryDatabase(this);
    this.manager = memory.manager;
    this.getRepository = target => memory.repository(target);
    this.transaction = (...args) => memory.transaction(args.at(-1));
    this.query = (...args) => memory.manager.query(...args);
    // Inspection is only reachable from inside this service's container.
    const server = require('node:http').createServer(async (request, response) => {
      try {
        const prefix = new URL(request.url, 'http://localhost').searchParams.get('prefix') || '';
        const result = await memory.exclusive(() => {
          const counts = {};
          const tables = {};
          for (const [name, rows] of memory.tables) {
            tables[name] = rows.size;
            if (name === 'Order') for (const row of rows.values()) {
              if (row.customerId.startsWith(prefix)) counts[row.status] = (counts[row.status] || 0) + 1;
            }
          }
          return { sampledAt: new Date().toISOString(), counts, tables, database: 'mock' };
        });
        response.setHeader('Content-Type', 'application/json');
        response.end(JSON.stringify(result));
      } catch (error) { response.statusCode = 500; response.end(String(error)); }
    });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(3999, '127.0.0.1', resolve); });
    this.destroy = async () => { await new Promise(resolve => server.close(resolve)); this.isInitialized = false; };
    this.isInitialized = true;
    console.log(JSON.stringify({ type: 'mock_database_initialized', database: this.options.database,
      durability: false, transactionModel: 'serialized in-process mutex', runId: process.env.RUN_ID }));
    return this;
  };
}
