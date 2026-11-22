#!/usr/bin/env node
/* Opt-in integration check. NEVER point this at an experiment database.
 * INDEX_TEST_PG_SOCKET=/tmp/disposable/socket node scripts/database/verify-indexes.cjs
 * INDEX_TEST_BUILD_ROOT may point at a temporary copy of implementation/.
 * Requires built services and their existing TypeORM dependencies.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const root = path.resolve(
  process.env.INDEX_TEST_BUILD_ROOT || "implementation",
);
if (!process.env.INDEX_TEST_PG_SOCKET) {
  console.error(
    "Set INDEX_TEST_PG_SOCKET to a disposable PostgreSQL Unix socket (database=test, user=test). No database accessed.",
  );
  process.exit(2);
}
const requireService = createRequire(
  path.join(root, "distributed-baseline/order-service/package.json"),
);
const { DataSource } = requireService("typeorm");
const architectures = [
  "distributed-baseline",
  "tcc",
  "two-phase-commit",
  "orchestrated-saga",
  "choreographed-saga",
];
const q = (name) => '"' + name.replaceAll('"', '""') + '"';
function entityFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    return entry.isDirectory()
      ? entityFiles(file)
      : entry.name.endsWith(".entity.js")
        ? [file]
        : [];
  });
}
function planIndexes(node) {
  return [
    node["Index Name"],
    ...(node.Plans || []).flatMap(planIndexes),
  ].filter(Boolean);
}
async function verify(architecture, service, ordinal) {
  const project = path.join(root, architecture, service + "-service");
  const entities = entityFiles(path.join(project, "dist"))
    .flatMap((file) => Object.values(require(file)))
    .filter((value) => typeof value === "function");
  const schema = `index_check_${process.pid}_${ordinal}`;
  const db = new DataSource({
    type: "postgres",
    host: process.env.INDEX_TEST_PG_SOCKET,
    username: "test",
    password: "test-only",
    database: "test",
    schema,
    entities,
    synchronize: false,
  });
  try {
    await db.initialize();
    await db.query(`CREATE SCHEMA ${q(schema)}`);
    await db.synchronize();
    const expected = db.entityMetadatas.flatMap((meta) =>
      meta.indices.map((index) => ({ meta, index })),
    );
    const required =
      service === "order"
        ? { IDX_order_items_order_id: ["orderId"] }
        : service === "billing"
          ? { IDX_payments_order_id: ["orderId"] }
          : service === "shipping"
            ? { IDX_shipments_order_id: ["orderId"] }
            : ["tcc", "two-phase-commit"].includes(architecture)
              ? { IDX_stock_reservations_product_status: ["productId", "status"] }
              : {};
    assert.equal(expected.length, Object.keys(required).length);
    for (const [name, columns] of Object.entries(required)) {
      const match = expected.find(({ index }) => index.name === name);
      assert(match, `Missing index ${name}`);
      assert.deepEqual(
        match.index.columns.map((column) => column.databaseName),
        columns,
      );
      assert.equal(
        match.index.isUnique,
        false,
        "Lookup indexes must not introduce new uniqueness rules",
      );
    }
    const physical = await db.query(
      "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname=$1",
      [schema],
    );
    for (const { index } of expected)
      assert(
        physical.some((row) => row.indexname === index.name),
        index.name,
      );
    const probes = [];
    for (const { meta, index } of expected) {
      const table = `${q(schema)}.${q(meta.tableName)}`;
      if (["payments", "shipments"].includes(meta.tableName)) {
        const columns =
          meta.tableName === "payments" ? '"orderId",amount' : '"orderId"';
        const values =
          meta.tableName === "payments" ? "'order-'||i,10" : "'order-'||i";
        await db.query(
          `INSERT INTO ${table} (${columns}) SELECT ${values} FROM generate_series(1,10000) i`,
        );
        probes.push({
          name: index.name,
          sql: `SELECT * FROM ${table} WHERE "orderId"='order-9999' LIMIT 1`,
        });
      } else if (meta.tableName === "stock_reservations") {
        await db.query(
          `INSERT INTO ${table} ("orderId","productId",quantity) SELECT 'order-'||i,'product-'||(i%100),1 FROM generate_series(1,10000) i`,
        );
        probes.push({
          name: index.name,
          sql: `SELECT * FROM ${table} WHERE "productId"='product-42' AND status='prepared'`,
        });
      }
      // Simulate the previous schema in this disposable database only.
      const runner = db.createQueryRunner();
      try {
        await runner.dropIndex(meta.tablePath, index.name);
      } finally {
        await runner.release();
      }
    }
    for (const probe of probes) {
      await db.query(
        `ANALYZE ${q(schema)}.${q(probe.name.includes("payments") ? "payments" : probe.name.includes("shipments") ? "shipments" : "stock_reservations")}`,
      );
      const plan = (
        await db.query(`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ${probe.sql}`)
      )[0]["QUERY PLAN"][0];
      probe.before = {
        root: plan.Plan["Node Type"],
        indexes: planIndexes(plan.Plan),
        executionMs: plan["Execution Time"],
      };
      assert.equal(
        probe.before.indexes.length,
        0,
        "Previous schema unexpectedly covers this lookup",
      );
    }
    const upgrade = await db.driver.createSchemaBuilder().log();
    assert(
      upgrade.upQueries.every((query) =>
        /^CREATE (UNIQUE )?INDEX /.test(query.query),
      ),
      "Upgrade must only add indexes",
    );
    assert.equal(upgrade.upQueries.length, expected.length);
    await db.synchronize();
    assert.equal(
      (await db.driver.createSchemaBuilder().log()).upQueries.length,
      0,
      "Repeated startup must be idempotent",
    );
    for (const probe of probes) {
      const plan = (
        await db.query(`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ${probe.sql}`)
      )[0]["QUERY PLAN"][0];
      assert(
        planIndexes(plan.Plan).includes(probe.name),
        `Planner did not choose ${probe.name}`,
      );
      console.log(
        JSON.stringify({
          architecture,
          service,
          index: probe.name,
          before: probe.before,
          after: planIndexes(plan.Plan),
          executionMs: plan["Execution Time"],
        }),
      );
    }
    console.log(
      JSON.stringify({
        architecture,
        service,
        verifiedIndexes: expected.length,
        upgradeOnlyAddsIndexes: true,
        repeatedStartupNoop: true,
      }),
    );
  } finally {
    if (db.isInitialized) {
      try {
        await db.query(`DROP SCHEMA IF EXISTS ${q(schema)} CASCADE`);
      } finally {
        await db.destroy();
      }
    }
  }
}
(async () => {
  let ordinal = 0;
  for (const architecture of architectures) {
    for (const service of ["order", "warehouse", "billing", "shipping"])
      await verify(architecture, service, ordinal++);
  }
})()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
