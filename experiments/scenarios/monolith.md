# Caso base — Monólito (análise)

Este repositório contém a implementação base de um e-commerce em arquitetura monolítica e serve como referência para experimentos comparativos (monólito vs. microsserviços). O foco das análises é avaliar três dimensões críticas: atomicidade transacional, latência de transação e integridade referencial.

## Objetivo

Documentar os testes realizados no monólito, apresentar os resultados observados e explicar causas e mitigação para inconsistências detectadas. Esses resultados funcionam como baseline para comparação com a versão distribuída.

## Ambiente e tecnologias

- Linguagem / framework: TypeScript + NestJS
- Banco de dados: PostgreSQL
- Orquestração local: Docker Compose (opcional)
- Seeds: [src/seed](src/seed)

Recomendações rápidas para execução local:

```
pnpm install   # ou npm install
pnpm run start:dev
```

## Casos de teste

Para cada cenário indicamos objetivo, procedimento, resultados e análise.

- **Atomicidade transacional**
  - Objetivo: confirmar que uma falha parcial não deixe efeitos residuais (ex.: estoque decrementado sem pagamento/pedido confirmado).
  - Procedimento: forçar erro na etapa de shipping e introduzir falha intermitente no billing. Payload de exemplo:

    ```json
    {
      "customerId": "cust-123",
      "productId": "ac40f13e-819b-43d1-b0c2-c3ea555a963c",
      "quantity": 1,
      "amount": 459.9
    }
    ```

  - Resultado: o pedido/pagamento foram revertidos, mas o estoque foi reduzido (24 → 22).
  - Análise: o `OrderService` iniciou transação com `queryRunner`, porém o `WarehouseService` não recebeu o mesmo `EntityManager` e executou em outra conexão. Solução: propagar `queryRunner.manager` para que todas as operações participem da mesma transação.

- **Latência transacional**
  - Objetivo: medir o tempo total de criação de pedido no monólito para servir de baseline.
  - Procedimento: executar múltiplas requisições ao endpoint de criação de pedidos (sem forçar erros) e medir tempos com `curl`/`k6`.
  - Resultados:
    - 100 reqs, 1 VU: avg=15.4ms min=8.33ms med=15.67ms max=25.87ms p(90)=17.76ms p(95)=18.27ms; throughput = 63.909243 req/s
    - 100 reqs, 5 VUs: avg=30.86ms min=9.62ms med=26.91ms max=107.55ms p(90)=53ms p(95)=61.92ms; throughput = 157.547626 req/s
  - Análise: chamadas in-process tornam latência baixa e estável; a decomposição em serviços adiciona overhead de rede e serialização.

- **Integridade referencial**
  - Objetivo: validar que o banco impede remoção de entidades referenciadas (ex.: deletar produto com order_items associados).
  - Procedimento: tentar deletar produto com referências existentes.
  - Resultado: operação rejeitada pelo PostgreSQL com erro de foreign key.
  - Análise: o banco relacional garante integridade automaticamente no monólito; em microsserviços, é necessário implementar garantias na camada da aplicação.

## Conclusões principais

- O monólito oferece garantias transacionais consistentes quando todas as operações usam o mesmo `EntityManager`/conexão.
- Falhas na propagação do `queryRunner` podem quebrar atomicidade mesmo em monólitos — revisar chamadas entre serviços internos.
- A versão distribuída perde essas garantias por padrão, exigindo padrões como sagas, compensações e orquestração, além de maior latência.

## Como reproduzir os testes

1. Inicie PostgreSQL (via Docker Compose ou instância local).
2. Rode seeds (ver [src/seed](src/seed)).
3. Execute a aplicação: `pnpm run start:dev`.
4. Exemplo de requisição ao endpoint de criação de pedidos:

```
curl -X POST http://localhost:3000/orders \
  -H 'Content-Type: application/json' \
  -d '{"customerId":"cust-123","productId":"ac40f13e-819b-43d1-b0c2-c3ea555a963c","quantity":1,"amount":459.9}'
```

Para testes de carga, utilize `bench-single.js` e `bench-concurrent.js` ou `k6`.
