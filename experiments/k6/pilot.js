import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

function positiveInteger(name, fallback) {
  const value = Number(__ENV[name] ?? fallback);
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`${name} deve ser um inteiro positivo.`);
  }
  return value;
}

// Aceita durações compostas do k6, por exemplo 1m, 30s ou 1m30s.
function durationSeconds(value) {
  const units = { ms: 0.001, s: 1, m: 60, h: 3600 };
  const parts = value.match(/(?:\d+(?:\.\d+)?)(?:ms|s|m|h)/g);
  if (!parts || parts.join('') !== value) {
    throw new Error('DURATION inválida. Use unidades ms, s, m ou h.');
  }
  const seconds = parts.reduce((total, part) => {
    const [, amount, unit] = part.match(/^(\d+(?:\.\d+)?)(ms|s|m|h)$/);
    return total + Number(amount) * units[unit];
  }, 0);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    throw new Error('DURATION deve ser maior que zero.');
  }
  return seconds;
}

const RATE = positiveInteger('RATE', 10);
const DURATION = __ENV.DURATION || '1m';
const DURATION_SECONDS = durationSeconds(DURATION);
const PRE_ALLOCATED_VUS = positiveInteger('PRE_ALLOCATED_VUS', 100);
const MAX_VUS = positiveInteger('MAX_VUS', 500);
const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8000';
const WAREHOUSE_PREFIX = __ENV.WAREHOUSE_PREFIX ?? '/warehouse';
const ARCHITECTURE = __ENV.ARCHITECTURE || 'unspecified';
const LOG_ERRORS = (__ENV.LOG_ERRORS ?? 'false').toLowerCase() === 'true';
const PHASE = __ENV.PILOT_PHASE || 'benchmark';

if (MAX_VUS < PRE_ALLOCATED_VUS) {
  throw new Error('MAX_VUS deve ser >= PRE_ALLOCATED_VUS.');
}

const startedRequests = new Counter('started_requests');
const createdOrders = new Counter('created_orders');
const technicalFailures = new Rate('technical_failure_rate');
const serverErrors = new Counter('server_errors');
const clientTimeouts = new Counter('client_timeouts');
const successfulWaiting = new Trend('successful_request_waiting', true);
const successfulDuration = new Trend('successful_request_duration', true);
const scenarioStart = new Trend('pilot_scenario_start_ms');
const requestEnd = new Trend('pilot_request_end_ms');

export const options = {
  scenarios: {
    pilot_orders: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: PRE_ALLOCATED_VUS,
      maxVUs: MAX_VUS,
      gracefulStop: '30s',
    },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(95)', 'p(99)'],
  // Limites neutros materializam as submétricas no handleSummary sem
  // escolher antecipadamente um critério de capacidade/saturação.
  thresholds: {
    [`http_reqs{phase:${PHASE}}`]: ['count>=0'],
    [`http_req_duration{phase:${PHASE}}`]: ['p(99)>=0'],
    [`http_req_waiting{phase:${PHASE}}`]: ['p(99)>=0'],
    [`http_req_failed{phase:${PHASE}}`]: ['rate>=0'],
  },
};

export function setup() {
  const res = http.get(`${BASE_URL}${WAREHOUSE_PREFIX}/products`, {
    timeout: '10s',
    tags: { endpoint: 'list-products', phase: 'setup' },
  });
  if (res.status !== 200) {
    throw new Error(`Falha ao buscar produtos. Status: ${res.status}`);
  }
  const products = res.json();
  if (!Array.isArray(products) || products.length === 0) {
    throw new Error('Nenhum produto encontrado. Execute o seed antes do piloto.');
  }
  return {
    products: products.map((product) => ({ id: product.id, price: product.price })),
    // Gerado uma vez no setup e compartilhado pelos VUs, inclusive em
    // execuções diretas que não informem RUN_ID.
    runId: __ENV.RUN_ID || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
  };
}

export default function (data) {
  const iteration = exec.scenario.iterationInTest;
  const product = data.products[iteration % data.products.length];
  const requestId = `k6-${ARCHITECTURE}-${data.runId}-rate${RATE}-${iteration}`;
  scenarioStart.add(exec.scenario.startTime);
  startedRequests.add(1);
  const res = http.post(`${BASE_URL}/orders`, JSON.stringify({
    customerId: `customer-${requestId}`,
    productId: product.id,
    quantity: 1,
    amount: product.price,
  }), {
    headers: {
      'Content-Type': 'application/json', 'X-Request-Id': requestId,
      'X-Pilot-Stage': data.runId,
    },
    timeout: '20s',
    redirects: 0,
    responseCallback: http.expectedStatuses(201),
    tags: { endpoint: 'create-order', phase: PHASE, offered_rate: String(RATE) },
  });
  requestEnd.add(Date.now());
  const accepted = res.status === 201;
  technicalFailures.add(!accepted);
  createdOrders.add(accepted ? 1 : 0);
  serverErrors.add(res.status >= 500 ? 1 : 0);
  clientTimeouts.add(
    res.status === 0 && (res.error_code === 1050 || /timeout/i.test(res.error || '')) ? 1 : 0,
  );
  if (accepted) {
    successfulWaiting.add(res.timings.waiting);
    successfulDuration.add(res.timings.duration);
  }
  check(res, { 'HTTP 201: pedido aceito': (response) => response.status === 201 });
  if (!accepted && LOG_ERRORS) {
    console.error(JSON.stringify({
      requestId, iteration, status: res.status, error: res.error,
      duration: res.timings.duration, waiting: res.timings.waiting,
    }));
  }
}

export function handleSummary(data) {
  const values = (name) => data.metrics[name]?.values || {};
  const count = (name) => values(name).count || 0;
  const latency = values(`http_req_duration{phase:${PHASE}}`);
  const successfulLatency = values('successful_request_duration');
  const started = count('started_requests');
  const requests = count(`http_reqs{phase:${PHASE}}`);
  const accepted = count('created_orders');
  const startMs = values('pilot_scenario_start_ms').min;
  const endMs = values('pilot_request_end_ms').max;
  // A chegada ocorre durante DURATION. O denominador do throughput HTTP
  // inclui a drenagem das últimas requisições, mas exclui o setup.
  const observationSeconds = started > 0 && Number.isFinite(endMs - startMs)
    ? Math.max(DURATION_SECONDS, (endMs - startMs) / 1000)
    : DURATION_SECONDS;
  const summary = {
    architecture: ARCHITECTURE,
    phase: PHASE,
    run_id: __ENV.RUN_ID || null,
    repetition: __ENV.REPETITION ? Number(__ENV.REPETITION) : null,
    rate: RATE,
    duration: DURATION,
    duration_seconds: DURATION_SECONDS,
    observation_seconds: observationSeconds,
    scenario_start_ms: Number.isFinite(startMs) ? startMs : null,
    request_end_ms: Number.isFinite(endMs) ? endMs : null,
    expected_iterations: RATE * DURATION_SECONDS,
    started_requests: started,
    requests,
    iterations: count('iterations'),
    accepted_orders: accepted,
    started_requests_per_second: started / DURATION_SECONDS,
    throughput: requests / observationSeconds,
    accepted_orders_per_second: accepted / observationSeconds,
    p50: requests ? latency.med ?? null : null,
    p95: requests ? latency['p(95)'] ?? null : null,
    p99: requests ? latency['p(99)'] ?? null : null,
    latency_mean_ms: requests ? latency.avg ?? null : null,
    successful_latency_mean_ms: accepted ? successfulLatency.avg ?? null : null,
    successful_p50_ms: accepted ? successfulLatency.med ?? null : null,
    successful_p95_ms: accepted ? successfulLatency['p(95)'] ?? null : null,
    successful_p99_ms: accepted ? successfulLatency['p(99)'] ?? null : null,
    error_rate: requests ? values('technical_failure_rate').rate ?? null : null,
    http_error_rate: requests ? values(`http_req_failed{phase:${PHASE}}`).rate ?? null : null,
    timeouts: count('client_timeouts'),
    server_errors: count('server_errors'),
    dropped_iterations: count('dropped_iterations'),
    pre_allocated_vus: PRE_ALLOCATED_VUS,
    max_vus: MAX_VUS,
    observed_vus_max: values('vus').max ?? null,
    semantics: {
      latency: 'HTTP apenas; p50/p95/p99 em ms, sem latência fim a fim da transação',
      accepted_orders: 'HTTP 201; não comprova conclusão da transação de negócio',
      dropped_iterations: 'Não iniciadas; investigar VUs, serviço e saturação do gerador',
    },
    metrics: data.metrics,
  };
  const json = `${JSON.stringify(summary, null, 2)}\n`;
  const output = { stdout: json };
  if (__ENV.SUMMARY_JSON) output[__ENV.SUMMARY_JSON] = json;
  return output;
}
