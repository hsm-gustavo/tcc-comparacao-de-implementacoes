import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: {
    ramp: {
      executor: 'ramping-vus',
      stages: [
        { duration: '30s', target: 10 },
        { duration: '30s', target: 20 },
        { duration: '30s', target: 50 },
        { duration: '30s', target: 0 },
      ],
    },
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
