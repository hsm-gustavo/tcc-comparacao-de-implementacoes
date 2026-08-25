import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8000';
const WAREHOUSE_PREFIX = __ENV.WAREHOUSE_PREFIX ?? '/warehouse';
const RATE = 35;

const createdOrders = new Counter('created_orders');

const technicalFailures = new Rate(
  'technical_failure_rate',
);

const successfulDuration = new Trend(
  'successful_request_duration',
  true,
);

const serverErrors = new Counter('server_errors');
const clientTimeouts = new Counter('client_timeouts');

export const options = {
  scenarios: {
    orders: {
      executor: 'constant-arrival-rate',

      rate: RATE,
      timeUnit: '1s',
      duration: '2m',

      preAllocatedVUs: 500,
      maxVUs: 500,

      gracefulStop: '30s',
    },
  },

  thresholds: {
    dropped_iterations: ['count==0'],
    technical_failure_rate: ['rate<0.01'],
  },
};

export function setup() {
  const res = http.get(
    `${BASE_URL}${WAREHOUSE_PREFIX}/products`,
    {
      timeout: '10s',
      tags: {
        endpoint: 'list-products',
      },
    },
  );

  if (res.status !== 200) {
    throw new Error(
      `Falha ao buscar produtos. Status: ${res.status}`,
    );
  }

  const products = res.json();

  if (
    !Array.isArray(products) ||
    products.length === 0
  ) {
    throw new Error(
      'Nenhum produto encontrado. Execute o seed antes do benchmark.',
    );
  }

  return {
    products: products.map((product) => ({
      id: product.id,
      price: product.price,
    })),
  };
}

export default function (data) {
  const iteration =
    exec.scenario.iterationInTest;

  const product =
    data.products[
      iteration % data.products.length
    ];

  const requestId =
    `k6-medium-${exec.vu.idInTest}-${iteration}`;

  const payload = JSON.stringify({
    customerId: `customer-${iteration}`,
    productId: product.id,
    quantity: 1,
    amount: product.price,
  });

  const res = http.post(
    `${BASE_URL}/orders`,
    payload,
    {
      headers: {
        'Content-Type': 'application/json',
        'X-Request-Id': requestId,
      },

      timeout: '20s',

      tags: {
        endpoint: 'create-order',
        load: 'medium',
      },
    },
  );

  const success = res.status === 201;

  technicalFailures.add(!success);

  if (success) {
    createdOrders.add(1);

    successfulDuration.add(
      res.timings.duration,
    );
  }

  if (res.status >= 500) {
    serverErrors.add(1);
  }

  if (
    res.status === 0 &&
    res.error
      ?.toLowerCase()
      .includes('timeout')
  ) {
    clientTimeouts.add(1);
  }

  check(res, {
    'status is 201': (response) =>
      response.status === 201,
  });

  if (!success) {
    console.error(
      JSON.stringify({
        requestId,
        iteration,
        productId: product.id,
        status: res.status,
        error: res.error,
        errorCode: res.error_code,
        body: res.body,
        duration: res.timings.duration,
        waiting: res.timings.waiting,
      }),
    );
  }
}
