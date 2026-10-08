import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8000';
const WAREHOUSE_PREFIX = __ENV.WAREHOUSE_PREFIX ?? '/warehouse';

const USERS = Number(__ENV.USERS || 100);

// desligar IO para nao atrapalhar os testes
const LOG_ERRORS =
  (__ENV.LOG_ERRORS ?? 'false')
    .toLowerCase() === 'true';

const createdOrders = new Counter('created_orders');

const successfulWaiting = new Trend(
  'successful_request_waiting',
  true,
);

// instante real de inicio e fim das reqs
const benchmarkRequestStartMs = new Trend(
  'benchmark_request_start_ms',
  false,
);

const benchmarkRequestEndMs = new Trend(
  'benchmark_request_end_ms',
  false,
);

const availability = new Rate(
  'availability',
);

const technicalFailures = new Rate(
  'technical_failure_rate',
);

const serverErrors = new Counter(
  'server_errors',
);

const clientTimeouts = new Counter(
  'client_timeouts',
);

export const options = {
  scenarios: {
    concurrent_orders: {
      executor: 'per-vu-iterations',
      vus: USERS,
      iterations: 1,
      maxDuration: '1m',
    },
  },

  thresholds: {
    technical_failure_rate: [
      'rate<0.01',
    ],
  },
};

export function setup() {
  const res = http.get(
      `${BASE_URL}${WAREHOUSE_PREFIX}/products`,
      {
        timeout: '10s',

        tags: {
          endpoint: 'list-products',
          phase: 'setup',
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
      products: products.map(
        (product) => ({
          id: product.id,
          price: product.price,
        }),
      ),
    };
  }

export default function (data) {
  const iteration = exec.scenario.iterationInTest;

  const product =
    data.products[
      iteration % data.products.length
    ];

  const requestId =
    `k6-${USERS}-users-${iteration}`;

  const payload = JSON.stringify({
    customerId:
      `customer-${USERS}-${iteration}`,

    productId: product.id,

    quantity: 1,

    amount: product.price,
  });

  const requestStartedAt = Date.now();

  benchmarkRequestStartMs.add(requestStartedAt)

  const res = http.post(
    `${BASE_URL}/orders`,
    payload,
    {
      headers: {
        'Content-Type':
          'application/json',

        'X-Request-Id':
          requestId,
      },

      timeout: '20s',

      tags: {
        endpoint:
          'create-order',

        concurrent_users:
          String(USERS),

        phase:
          'benchmark',
      },
    }
  );

  const requestEndedAt = Date.now();

  benchmarkRequestEndMs.add(requestEndedAt);

  const success = res.status === 201;

  availability.add(success);

  technicalFailures.add(
    !success,
  );

  if (success) {
    createdOrders.add(1);
    successfulWaiting.add(
      res.timings.waiting,
    );
  }

  if (res.status >= 500) {
    serverErrors.add(1);
  }

  if (
    res.status === 0 &&
    res.error?.toLowerCase()
      .includes('timeout')
  ) {
    clientTimeouts.add(1);
  }

  check(res, {
    'status is 201':
      (response) =>
        response.status === 201,
  });

  if (!success && LOG_ERRORS) {
    console.error(
      JSON.stringify({
        requestId,
        users: USERS,
        iteration,
        productId:
          product.id,

        status:
          res.status,

        error:
          res.error,

        errorCode:
          res.error_code,

        duration:
          res.timings.duration,

        waiting:
          res.timings.waiting,
      }),
    );
  }
}
