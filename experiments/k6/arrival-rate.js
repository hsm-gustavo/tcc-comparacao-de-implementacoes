import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:3000';
const WAREHOUSE_PREFIX = __ENV.WAREHOUSE_PREFIX ?? '/warehouse';
const RATE = Number(__ENV.RATE ?? 20);
const DURATION = __ENV.DURATION ?? '2m';
const PRE_ALLOCATED_VUS = Number(__ENV.PRE_ALLOCATED_VUS ?? 100);
const MAX_VUS = Number(__ENV.MAX_VUS ?? 300);
const LOG_ERRORS = (__ENV.LOG_ERRORS ?? 'false').toLowerCase() === 'true';

if (!Number.isInteger(RATE) || RATE < 1) throw new Error('RATE must be a positive integer');
if (!Number.isInteger(PRE_ALLOCATED_VUS) || PRE_ALLOCATED_VUS < 1) throw new Error('PRE_ALLOCATED_VUS must be positive');
if (!Number.isInteger(MAX_VUS) || MAX_VUS < PRE_ALLOCATED_VUS) throw new Error('MAX_VUS must be >= PRE_ALLOCATED_VUS');

const createdOrders = new Counter('created_orders');
const successfulDuration = new Trend('successful_request_duration', true);
const requestSuccessRate = new Rate('request_success_rate');
const technicalFailureRate = new Rate('technical_failure_rate');
const serverErrors = new Counter('server_errors');
const clientTimeouts = new Counter('client_timeouts');

export const options = {
  scenarios: {
    sustained_orders: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: PRE_ALLOCATED_VUS,
      maxVUs: MAX_VUS,
      gracefulStop: '30s',
    },
  },
};

export function setup() {
  const res = http.get(`${BASE_URL}${WAREHOUSE_PREFIX}/products`, {
    timeout: '10s',
    tags: { endpoint: 'list-products', phase: 'setup' },
  });
  if (res.status !== 200) throw new Error(`Product lookup failed: HTTP ${res.status}`);
  const products = res.json();
  if (!Array.isArray(products) || products.length === 0) {
    throw new Error('No products found; run seed.sh first');
  }
  return { products: products.map(({ id, price }) => ({ id, price })) };
}

export default function (data) {
  const iteration = exec.scenario.iterationInTest;
  const product = data.products[iteration % data.products.length];
  const requestId = `k6-rate${RATE}-${iteration}`;
  const res = http.post(`${BASE_URL}/orders`, JSON.stringify({
    customerId: `customer-rate${RATE}-${iteration}`,
    productId: product.id,
    quantity: 1,
    amount: product.price,
  }), {
    headers: { 'Content-Type': 'application/json', 'X-Request-Id': requestId },
    timeout: '20s',
    tags: { endpoint: 'create-order', phase: 'benchmark', offered_rate: String(RATE) },
  });

  const success = res.status === 201;
  requestSuccessRate.add(success);
  technicalFailureRate.add(!success);
  if (success) {
    createdOrders.add(1);
    successfulDuration.add(res.timings.duration);
  }
  if (res.status >= 500) serverErrors.add(1);
  if (res.status === 0 && /timeout/i.test(res.error ?? '')) clientTimeouts.add(1);
  check(res, { 'status is 201': (response) => response.status === 201 });
  if (!success && LOG_ERRORS) {
    console.error(JSON.stringify({ requestId, status: res.status, error: res.error,
      errorCode: res.error_code, duration: res.timings.duration }));
  }
}
