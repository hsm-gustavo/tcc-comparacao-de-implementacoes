import http from 'k6/http';
import { check } from 'k6';

export const options = {
  vus: 20, // ou 10, depende do hardware
  duration: '1m',
};

export function setup() {
  const res = http.get('http://localhost:3002/warehouse/products');

  if (res.status !== 200) {
    throw new Error(`Falha ao buscar produtos. Status: ${res.status}`);
  }

  const products = res.json();

  if (!Array.isArray(products) || products.length === 0) {
    throw new Error(
      'Nenhum produto encontrado. Execute o seed antes do benchmark.',
    );
  }

  const product = products.find(
    (p) => p.name === 'Teclado Mecânico',
  );

  return {
    productId: product.id,
    price: product.price
  };
}

export default function (data) {
  const payload = JSON.stringify({
    customerId: 'cust-123',
    productId: data.productId,
    quantity: 1,
    amount: data.price,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
  };

  const res = http.post(
    'http://localhost:3001/orders',
    payload,
    params,
  );

  const passed = check(res, {
    'status is 201': (r) => r.status === 201,
  });

  if (!passed) {
    console.error(`[FALHA] Status: ${res.status} | Body: ${res.body}`);
  }
}
