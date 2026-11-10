#!/usr/bin/env node
/* Controlled functional checks, not a load test. Only a disposable database:
 * FUNCTIONAL_TEST_PG_SOCKET=/tmp/socket [FUNCTIONAL_TEST_BUILD_ROOT=implementation]
 * node scripts/database/verify-functional-corrections.cjs
 * Requires compiled services and existing baseline order-service dependencies.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { createRequire } = require("node:module");
if (!process.env.FUNCTIONAL_TEST_PG_SOCKET) {
  console.error(
    "Set FUNCTIONAL_TEST_PG_SOCKET to a DISPOSABLE PostgreSQL Unix socket. Database=test, user=test. No database accessed.",
  );
  process.exit(2);
}
const root = path.resolve(
  process.env.FUNCTIONAL_TEST_BUILD_ROOT || "implementation",
);
const req = createRequire(
  path.join(root, "distributed-baseline/order-service/package.json"),
);
const { DataSource } = req("typeorm");
const { of, throwError } = req("rxjs");
const { AxiosError } = req("axios");
let schemaNumber = 0,
  passed = 0;
const load = (a, s, file) =>
  require(path.join(root, a, `${s}-service/dist/${s}`, `${file}.js`));
async function check(name, work) {
  await work();
  passed++;
  console.log(JSON.stringify({ test: name, passed: true }));
}
async function database(a, s, work) {
  const folder = path.join(root, a, `${s}-service/dist/${s}/entities`);
  const entities = fs
    .readdirSync(folder)
    .filter((f) => f.endsWith(".entity.js"))
    .flatMap((f) => Object.values(require(path.join(folder, f))))
    .filter((x) => typeof x === "function");
  const schema = `functional_${process.pid}_${schemaNumber++}`;
  const db = new DataSource({
    type: "postgres",
    host: process.env.FUNCTIONAL_TEST_PG_SOCKET,
    username: "test",
    password: "test-only",
    database: "test",
    schema,
    entities,
    synchronize: false,
  });
  try {
    await db.initialize();
    await db.query(`CREATE SCHEMA "${schema}"`);
    await db.synchronize();
    await work(db, schema);
  } finally {
    if (db.isInitialized) {
      try {
        await db.query(`DROP SCHEMA IF EXISTS "${schema}" CASCADE`);
      } finally {
        await db.destroy();
      }
    }
  }
}
const many = (n, work) =>
  Promise.all(Array.from({ length: n }, (_, i) => work(i)));
(async () => {
  for (const a of [
    "distributed-baseline",
    "orchestrated-saga",
    "tcc",
    "two-phase-commit",
  ]) {
    await database(a, "order", async (db, schema) => {
      const { Order } = load(a, "order", "entities/order.entity");
      const { OrderItem } = load(a, "order", "entities/order-item.entity");
      const { OrderService } = load(a, "order", "order.service");
      const orders = db.getRepository(Order),
        items = db.getRepository(OrderItem);
      const dto = {
        customerId: "check",
        productId: randomUUID(),
        quantity: 1,
        amount: 10,
      };
      let calls = [];
      let mode = "ok";
      const http = {
        patch: (...args) => request(...args),
        post: (...args) => request(...args),
      };
      function request(url) {
        calls.push(url);
        if (
          mode === "prepare-timeout" &&
          /\/(try-reserve|prepare-reserve)$/.test(url)
        )
          return throwError(() => new AxiosError("timeout", "ECONNABORTED"));
        if (
          mode === "confirm-timeout" &&
          /\/(confirm-reserve|commit-reserve)$/.test(url)
        )
          return throwError(() => new AxiosError("timeout", "ECONNABORTED"));
        if (
          mode === "cancel-timeout" &&
          /\/(try-reserve|prepare-reserve|cancel-reserve|rollback-reserve)$/.test(
            url,
          )
        )
          return throwError(() => new AxiosError("timeout", "ECONNABORTED"));
        if (mode === "remote-timeout" && url.endsWith("/reserve"))
          return throwError(() => new AxiosError("timeout", "ECONNABORTED"));
        if (
          ["definitive-failure", "compensation-failure"].includes(mode) &&
          url.endsWith("/shipping")
        ) {
          return throwError(
            () =>
              new AxiosError(
                "rejected",
                "ERR_BAD_REQUEST",
                undefined,
                undefined,
                {
                  status: 400,
                  data: { message: "rejected" },
                  headers: {},
                  config: {},
                },
              ),
          );
        }
        if (mode === "compensation-failure" && url.endsWith("/refund")) {
          return throwError(() => new AxiosError("timeout", "ECONNABORTED"));
        }
        return of({ data: {}, status: 200 });
      }
      const service = new OrderService(orders, items, http);
      await check(`${a}: item failure rolls back header`, async () => {
        await db.query(
          `ALTER TABLE "${schema}".order_items ADD CONSTRAINT test_item_failure CHECK(quantity<>1)`,
        );
        await assert.rejects(service.createOrder(dto), /test_item_failure/);
        assert.equal(await orders.count(), 0);
        assert.equal(await items.count(), 0);
        assert.equal(calls.length, 0);
        await db.query(
          `ALTER TABLE "${schema}".order_items DROP CONSTRAINT test_item_failure`,
        );
      });
      await check(`${a}: normal flow commits header and item`, async () => {
        const result = await service.createOrder(dto);
        assert.equal(result.status, "confirmed");
        assert.equal(await orders.count(), 1);
        assert.equal(await items.count(), 1);
      });
      if (["tcc", "two-phase-commit"].includes(a)) {
        for (const phase of [
          "prepare-timeout",
          "confirm-timeout",
          "cancel-timeout",
        ]) {
          await check(
            `${a}: ${phase} respects decision and uncertainty`,
            async () => {
              mode = phase;
              calls = [];
              const customerId = randomUUID();
              await assert.rejects(service.createOrder({ ...dto, customerId }));
              const order = await orders.findOneByOrFail({ customerId });
              assert.equal(
                order.status,
                phase === "prepare-timeout" ? "cancelled" : "pending",
              );
              if (phase === "confirm-timeout")
                assert.equal(
                  calls.filter((x) =>
                    /\/(cancel|cancel-reserve|rollback|rollback-reserve)$/.test(
                      x,
                    ),
                  ).length,
                  0,
                );
            },
          );
        }
      }
      if (a === "orchestrated-saga") {
        for (const failure of ["definitive-failure", "compensation-failure"]) {
          await check(
            `orchestrated: ${failure} reports cancellation truthfully`,
            async () => {
              mode = failure;
              calls = [];
              const customerId = randomUUID();
              await assert.rejects(service.createOrder({ ...dto, customerId }));
              assert.equal(
                (await orders.findOneByOrFail({ customerId })).status,
                failure === "definitive-failure" ? "cancelled" : "pending",
              );
              assert(calls.some((x) => x.endsWith("/refund")));
              assert(calls.some((x) => x.endsWith("/release")));
            },
          );
        }
        await check(
          "orchestrated: timeout with unknown remote result does not claim cancellation",
          async () => {
            mode = "remote-timeout";
            const customerId = randomUUID();
            await assert.rejects(service.createOrder({ ...dto, customerId }));
            assert.equal(
              (await orders.findOneByOrFail({ customerId })).status,
              "pending",
            );
          },
        );
      }
    });
  }
  for (const a of ["tcc", "two-phase-commit"]) {
    await database(a, "warehouse", async (db, schema) => {
      const { Product } = load(a, "warehouse", "entities/product.entity");
      const { StockReservation } = load(
        a,
        "warehouse",
        "entities/stock-reservation.entity",
      );
      const { WarehouseService } = load(a, "warehouse", "warehouse.service");
      const products = db.getRepository(Product),
        reservations = db.getRepository(StockReservation);
      const service = new WarehouseService(products, reservations);
      await check(`${a}: concurrent Prepare cannot over-reserve`, async () => {
        const p = await service.create({
          name: "one unit",
          price: 10,
          stockQuantity: 1,
        });
        const all = await Promise.allSettled(
          Array.from({ length: 10 }, () =>
            service.prepareStockReservation({
              orderId: randomUUID(),
              productId: p.id,
              quantity: 1,
            }),
          ),
        );
        assert.equal(all.filter((x) => x.status === "fulfilled").length, 1);
        assert.equal(await reservations.countBy({ productId: p.id }), 1);
      });
      await check(
        `${a}: concurrent Confirm loses no stock and is idempotent`,
        async () => {
          const p = await service.create({
            name: "ten units",
            price: 10,
            stockQuantity: 10,
          });
          const prepared = await many(10, () =>
            service.prepareStockReservation({
              orderId: randomUUID(),
              productId: p.id,
              quantity: 1,
            }),
          );
          await Promise.all(
            prepared.flatMap((r) => [
              service.commitStockReservation({ orderId: r.orderId }),
              service.commitStockReservation({ orderId: r.orderId }),
            ]),
          );
          assert.equal(
            (await products.findOneByOrFail({ id: p.id })).stockQuantity,
            0,
          );
        },
      );
      await check(
        `${a}: failed reservation update rolls back stock decrement`,
        async () => {
          const p = await service.create({
            name: "rollback",
            price: 10,
            stockQuantity: 2,
          });
          const r = await service.prepareStockReservation({
            orderId: randomUUID(),
            productId: p.id,
            quantity: 1,
          });
          await db.query(
            `ALTER TABLE "${schema}".stock_reservations ADD CONSTRAINT test_commit_failure CHECK(status<>'committed') NOT VALID`,
          );
          await assert.rejects(
            service.commitStockReservation({ orderId: r.orderId }),
            /test_commit_failure/,
          );
          assert.equal(
            (await products.findOneByOrFail({ id: p.id })).stockQuantity,
            2,
          );
          assert.equal(
            (await reservations.findOneByOrFail({ id: r.id })).status,
            "prepared",
          );
          await db.query(
            `ALTER TABLE "${schema}".stock_reservations DROP CONSTRAINT test_commit_failure`,
          );
        },
      );
      await check(
        `${a}: empty cancellation prevents late Prepare`,
        async () => {
          const p = await service.create({
            name: "late",
            price: 10,
            stockQuantity: 1,
          });
          const orderId = randomUUID();
          await service.rollbackStockReservation({ orderId });
          await assert.rejects(
            service.prepareStockReservation({
              orderId,
              productId: p.id,
              quantity: 1,
            }),
          );
          assert.equal(
            (await products.findOneByOrFail({ id: p.id })).stockQuantity,
            1,
          );
        },
      );
      await check(
        `${a}: concurrent Confirm and Cancel select one terminal state`,
        async () => {
          const p = await service.create({
            name: "race",
            price: 10,
            stockQuantity: 1,
          });
          const orderId = randomUUID();
          await service.prepareStockReservation({
            orderId,
            productId: p.id,
            quantity: 1,
          });
          const outcomes = await Promise.allSettled([
            service.commitStockReservation({ orderId }),
            service.rollbackStockReservation({ orderId }),
          ]);
          assert.equal(
            outcomes.filter((x) => x.status === "fulfilled").length,
            1,
          );
          const r = await reservations.findOneByOrFail({ orderId });
          assert.equal(
            (await products.findOneByOrFail({ id: p.id })).stockQuantity,
            r.status === "committed" ? 0 : 1,
          );
        },
      );
    });
    for (const s of ["billing", "shipping"]) {
      await database(a, s, async (db) => {
        const isBilling = s === "billing";
        const entity = isBilling
          ? load(a, s, "entities/payment.entity").Payment
          : load(a, s, "entities/shipment.entity").Shipment;
        const Service = isBilling
          ? load(a, s, "billing.service").BillingService
          : load(a, s, "shipping.service").ShippingService;
        const repo = db.getRepository(entity),
          service = new Service(repo);
        const prepare = (dto) =>
          isBilling
            ? service.preparePayment(dto)
            : service.prepareShipment(dto);
        const commit = (id) =>
          isBilling ? service.commitPayment(id) : service.commitShipment(id);
        const cancel = (id) =>
          isBilling
            ? service.rollbackPayment(id)
            : service.rollbackShipment(id);
        await check(
          `${a}/${s}: duplicate Prepare and Confirm apply once`,
          async () => {
            const orderId = randomUUID();
            await many(10, () => prepare({ orderId, amount: 10 }));
            assert.equal(await repo.countBy({ orderId }), 1);
            const results = await many(10, () => commit(orderId));
            assert.equal(new Set(results.map((x) => x.id)).size, 1);
            if (!isBilling)
              assert.equal(new Set(results.map((x) => x.trackingCode)).size, 1);
            await assert.rejects(cancel(orderId));
          },
        );
        await check(
          `${a}/${s}: empty Cancel is durable and terminal`,
          async () => {
            const orderId = randomUUID();
            await cancel(orderId);
            await cancel(orderId);
            await assert.rejects(prepare({ orderId, amount: 10 }));
            await assert.rejects(commit(orderId));
            assert.equal(await repo.countBy({ orderId }), 1);
          },
        );
      });
    }
  }
  for (const a of ["distributed-baseline", "orchestrated-saga"]) {
    await database(a, "warehouse", async (db) => {
      const { Product } = load(a, "warehouse", "entities/product.entity");
      const { WarehouseService } = load(a, "warehouse", "warehouse.service");
      const repo = db.getRepository(Product),
        service = new WarehouseService(repo);
      await check(`${a}: releases cannot overwrite reservations`, async () => {
        const p = await service.create({
          name: "concurrent",
          price: 10,
          stockQuantity: 100,
        });
        await Promise.all([
          many(20, () =>
            service.releaseStock({ productId: p.id, quantity: 1 }),
          ),
          many(20, () =>
            service.reserveStock({ productId: p.id, quantity: 1 }),
          ),
        ]);
        assert.equal(
          (await repo.findOneByOrFail({ id: p.id })).stockQuantity,
          100,
        );
      });
    });
  }
  await database("choreographed-saga", "order", async (db) => {
    const { Order } = load(
      "choreographed-saga",
      "order",
      "entities/order.entity",
    );
    const { OrderItem } = load(
      "choreographed-saga",
      "order",
      "entities/order-item.entity",
    );
    const { OrderService } = load(
      "choreographed-saga",
      "order",
      "order.service",
    );
    const repo = db.getRepository(Order),
      service = new OrderService(repo);
    await check(
      "choreographed: concurrent events never overwrite terminal states",
      async () => {
        const order = await repo.save(
          repo.create({ customerId: "terminal", totalAmount: 10 }),
        );
        await Promise.all([
          service.markConfirmed({ orderId: order.id }),
          service.markCancelled({ orderId: order.id }),
        ]);
        const terminal = (await repo.findOneByOrFail({ id: order.id })).status;
        await many(10, () =>
          terminal === "confirmed"
            ? service.markCancelled({ orderId: order.id })
            : service.markConfirmed({ orderId: order.id }),
        );
        assert.equal(
          (await repo.findOneByOrFail({ id: order.id })).status,
          terminal,
        );
      },
    );
    await check(
      "choreographed: publication uncertainty keeps order recoverable by a late event",
      async () => {
        service.emit = async () => {
          throw new Error("confirmation lost");
        };
        const customerId = randomUUID();
        await assert.rejects(
          service.createOrder({
            customerId,
            productId: randomUUID(),
            quantity: 1,
            amount: 10,
          }),
        );
        const order = await repo.findOneByOrFail({ customerId });
        assert.equal(order.status, "pending");
        assert.equal(
          await db
            .getRepository(OrderItem)
            .countBy({ order: { id: order.id } }),
          1,
        );
        await service.markConfirmed({ orderId: order.id });
        assert.equal(
          (await repo.findOneByOrFail({ id: order.id })).status,
          "confirmed",
        );
      },
    );
  });
  console.log(JSON.stringify({ passed, complete: true }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
