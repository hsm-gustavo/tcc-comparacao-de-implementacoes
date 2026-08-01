import http from 'k6/http';
import { check } from 'k6';

const VUS = Number(__ENV.VUS ?? 1);
const DURATION = __ENV.DURATION ?? '30s';

export const options = {
  vus: VUS,
  duration: DURATION,
  gracefulStop: '10s',
  summaryTrendStats: [
    'avg',
    'min',
    'med',
    'max',
    'p(90)',
    'p(95)',
    'count',
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
  },
};

export function setup() {
  const res = http.get('http://localhost:3000/warehouse/products')

  if (res.status !== 200) {
    throw new Error(`Falha ao buscar produtos na etapa de setup. Status: ${res.status}`);
  }

  const products = res.json();
  if (!Array.isArray(products) || products.length === 0) {
    throw new Error('Nenhum produto encontrado no banco para rodar o benchmark. Execute o seed primeiro.');
  }

  const targetProduct = products[Math.floor(Math.random() * products.length)];

  return {
    productId: targetProduct.id,
  }
}

export default function (data) {
  const payload = JSON.stringify({
    customerId: 'cust-123',
    productId: data.productId,
    quantity: 1,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
  };

  const res = http.post('http://localhost:3000/orders', payload, params);

  check(res, {
    'status is 201 or 200': (r) => r.status === 201 || r.status === 200,
  });
}
