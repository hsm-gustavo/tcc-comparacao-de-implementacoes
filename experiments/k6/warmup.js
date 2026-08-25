import http from 'k6/http';
import exec from 'k6/execution';

const BASE_URL =
  __ENV.BASE_URL || 'http://127.0.0.1:3000';

const WAREHOUSE_PREFIX =
  __ENV.WAREHOUSE_PREFIX ?? '/warehouse';

export const options = {
  scenarios: {
    warmup: {
      executor: 'per-vu-iterations',
      vus: 20,
      iterations: 1,
      maxDuration: '30s',
    },
  },
};

export function setup() {
  const res = http.get(
    `${BASE_URL}${WAREHOUSE_PREFIX}/products`,
    {
      timeout: '10s',
    },
  );

  if (res.status !== 200) {
    throw new Error(
      `Warmup setup failed: ${res.status}`,
    );
  }

  return {
    products: res.json(),
  };
}

export default function (data) {
  const iteration =
    exec.scenario.iterationInTest;

  const product =
    data.products[
      iteration % data.products.length
    ];

  http.post(
    `${BASE_URL}/orders`,
    JSON.stringify({
      customerId:
        `warmup-customer-${iteration}`,
      productId: product.id,
      quantity: 1,
      amount: product.price,
    }),
    {
      headers: {
        'Content-Type': 'application/json',
      },
      timeout: '20s',
    },
  );
}
