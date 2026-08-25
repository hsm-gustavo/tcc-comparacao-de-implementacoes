import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import {
  Counter,
  Rate,
  Trend,
} from 'k6/metrics';

const createdOrders = new Counter('created_orders');
const serverErrors = new Counter('server_errors');
const timeoutErrors = new Counter('timeout_errors');
const successfulRequestDuration = new Trend(
  'successful_request_duration',
  true,
);
const technicalFailureRate = new Rate(
  'technical_failure_rate',
);

export const options = {
  scenarios: {
    orders: {
      executor: 'constant-arrival-rate',

      rate: Number(__ENV.RATE),
      timeUnit: '1s',
      duration: '2m',

      preAllocatedVUs: 500,
      maxVUs: 500,

      /* stages: [
        { target: 25, duration: '30s' },
        { target: 50, duration: '30s' },
        { target: 100, duration: '30s' },
        { target: 150, duration: '30s' },
        { target: 200, duration: '30s' },
        { target: 250, duration: '30s' },
        { target: 300, duration: '30s' },
      ], */

      gracefulStop: '30s',
    },
  },
};

export function setup() {
  const res = http.get(
    'http://127.0.0.1:8000/warehouse/products',
  );

  if (res.status !== 200) {
    throw new Error(
      `Falha ao buscar produtos: ${res.status}`,
    );
  }

  const products = res.json();

  if (!Array.isArray(products) || products.length === 0) {
    throw new Error('Nenhum produto encontrado.');
  }

  return {
    products: products.map((product) => ({
      id: product.id,
      price: product.price,
    })),
  };
}

export default function (data) {
  const iteration = exec.scenario.iterationInTest;
  const product =
    data.products[iteration % data.products.length];

  const payload = JSON.stringify({
    customerId: `customer-${iteration}`,
    productId: product.id,
    quantity: 1,
    amount: product.price,
  });

  const res = http.post(
    'http://127.0.0.1:8000/orders',
    payload,
    {
      headers: {
        'Content-Type': 'application/json',
        'X-Request-Id': `k6-${iteration}`,
      },
      timeout: '20s',
    },
  );

  const success = res.status === 201;
  const timeout =
    res.status === 504 ||
    res.body?.includes('timeout');

  technicalFailureRate.add(!success);

  if (success) {
    createdOrders.add(1);
    successfulRequestDuration.add(res.timings.duration);
  }

  if (res.status >= 500) {
    serverErrors.add(1);

    if (timeout) {
      timeoutErrors.add(1);
    }

    console.error(
      JSON.stringify({
        iteration,
        productId: product.id,
        status: res.status,
        body: res.body,
        duration: res.timings.duration,
        waiting: res.timings.waiting,
      }),
    );
  }

  check(res, {
    'status is 201': (r) => r.status === 201,
  });
}
