# Experimento 1: Auditoria e Rastreabilidade

## Objetivo

Verificar a capacidade do sistema de preservar o histório completo das alterações realizadas durante uma transação distribuída.

## Procedimento

Vamos criar um pedido e observar o histórico de eventos relacionados a esse pedido.

## Resultados

1. Primeiro criamos o pedido:

```json
{
  "order_events": [
    {
      "id": "ca80dfa7-bf0f-4fc3-9711-d53391253894",
      "aggregateId": "c7a5c30c-5b86-4216-975b-2e1b3d5ce9ce",
      "type": "OrderPlaced",
      "data": "{\"amount\": 249.9, \"quantity\": 1, \"productId\": \"bd6ab524-8202-4df8-be4d-b52a61eefea6\", \"customerId\": \"cust-123\"}",
      "createdAt": "2026-06-05T19:26:02.294Z"
    }
  ]
}
```

2. Após criar o pedido enviamos uma mensagem para o Warehouse para reservar o estoque do produto. O Warehouse processa a mensagem e registra um evento de "StockDecreased" indicando que o estoque do produto foi reduzido para atender ao pedido:

```json
{
  "warehouse_events": [
    {
      "id": "cbbb222f-f0d3-40e6-a162-586de3c11f08",
      "aggregateId": "bd6ab524-8202-4df8-be4d-b52a61eefea6",
      "type": "StockDecreased",
      "data": "{\"orderId\": \"c7a5c30c-5b86-4216-975b-2e1b3d5ce9ce\", \"quantity\": 1}",
      "createdAt": "2026-06-05T19:26:02.376Z"
    }
  ]
}
```

3. O Warehouse então envia uma mensagem para o Billing, indicando que o pedido foi processado e que o pagamento pode ser cobrado. O Billing primeiramente registra o pagamento como pendente, e depois de processar o pagamento, registra um evento de "PaymentCompleted" indicando que o pagamento foi concluído com sucesso:

```json
{
  "billing_events": [
    {
      "id": "261c380c-2220-40ea-8533-8b34b5805e8d",
      "aggregateId": "c7a5c30c-5b86-4216-975b-2e1b3d5ce9ce",
      "type": "PaymentPending",
      "data": "{\"amount\": 249.9}",
      "createdAt": "2026-06-05T19:26:02.470Z"
    },
    {
      "id": "d0d328f3-91e4-4a63-bb6a-c9e611c54189",
      "aggregateId": "c7a5c30c-5b86-4216-975b-2e1b3d5ce9ce",
      "type": "PaymentCompleted",
      "data": "{\"amount\": 249.9}",
      "createdAt": "2026-06-05T19:26:02.482Z"
    }
  ]
}
```

4. O Billing então envia uma mensagem para o Shipping, indicando que o pagamento foi concluído e que o pedido pode ser enviado. O Shipping registra um evento de "ShipmentRequest" indicando que foi solicitado o envio do pedido, e depois de processar o envio, registra um evento de "ShipmentDispatched" indicando que o pedido foi enviado com sucesso:

```json
{
  "shipping_events": [
    {
      "id": "3602e553-dba4-4660-9cdd-77f4f138ee9a",
      "aggregateId": "c7a5c30c-5b86-4216-975b-2e1b3d5ce9ce",
      "type": "ShipmentRequested",
      "data": "{}",
      "createdAt": "2026-06-05T19:26:02.569Z"
    },
    {
      "id": "c28fa415-d906-45dd-a0df-52d0c04271d8",
      "aggregateId": "c7a5c30c-5b86-4216-975b-2e1b3d5ce9ce",
      "type": "ShipmentDispatched",
      "data": "{\"trackingCode\": \"BR-UG9RWZD\"}",
      "createdAt": "2026-06-05T19:26:02.581Z"
    }
  ]
}
```

5. Finalmente, o Shipping envia uma mensagem para o Order para conectar o envio ao pedido:

```json
{
  "order_events": [
    {
      "id": "26ba124c-3631-454f-8b9b-acd866193f25",
      "aggregateId": "c7a5c30c-5b86-4216-975b-2e1b3d5ce9ce",
      "type": "OrderShipmentLinked",
      "data": "{\"trackingCode\": \"BR-UG9RWZD\"}",
      "createdAt": "2026-06-05T19:26:02.654Z"
    }
  ]
}
```

## Análise

Diferente da implementação distribuída base, na qual apenas o estado final das entidades é persistido, o Event Sourcing registra explicitamente cada mudança de estado como um evento imutável. Como resultado, torna-se possível reconstruir a sequência exata de operações realizadas durante o processamento de um pedido, permitindo auditoria completa, rastreabilidade e investigação de falhas. Além disso, a presença do histórico de eventos facilita a compreensão do comportamento do sistema ao longo do tempo, característica particularmente relevante em ambientes distribuídos.

# Experimento 2: Recuperação de Estado

## Objetivo

Verificar se o estado do sistema pode ser reconstruído a partir dos eventos persistidos.

## Procedimento

1. Criar um pedido completo.
2. Executar o replay para reconstruir as tabelas de domínio.
3. Comparar os dados reconstruídos com os eventos armazenados.

## Resultados

O endpoint de replay foi capaz de reconstruir corretamente as entidades de domínio a partir dos eventos armazenados. Para cada evento processado, o sistema aplicou a transformação correspondente nas tabelas de domínio utilizando operações de inserção ou atualização. Dessa forma, o estado final reconstruído foi equivalente ao estado originalmente produzido pela execução da transação.

## Análise

Essa capacidade de reconstrução é uma das principais vantagens do event sourcing. Ela garante que mesmo em caso de falhas catastróficas, onde o estado atual do serviço é perdido, podemos recuperar completamente o estado a partir dos eventos. Isso aumenta significativamente a resiliência do sistema e reduz o risco de perda de dados.

Essa característica constitui uma das principais diferenças em relação às abordagens tradicionais, nas quais a perda do estado persistido normalmente implica perda definitiva das informações. No modelo baseado em eventos, o histórico torna-se a fonte de verdade do sistema, permitindo reconstruções futuras sempre que necessário.

# Experimento 3: Impacto no Armazenamento

## Objetivo

Comparar a quantidade de dados persistidos entre a abordagem tradicional e Event Sourcing.

## Análise

Supondo a criação de 100 pedidos seguindo o fluxo de sucesso, a implementação distribuída base produziria aproximadamente 100 registros de pedido, 100 registros de pagamento e 100 registros de envio, além das atualizações realizadas sobre o estoque.

No modelo de Event Sourcing, cada alteração de estado gera um novo evento persistido. Considerando os eventos produzidos neste experimento, cada pedido gera:

- 2 eventos de Order Service
- 1 evento de Warehouse Service
- 2 eventos de Billing Service
- 2 eventos de Shipping Service

Isso resulta em 7 eventos por pedido, totalizando cerca de 700 eventos para os mesmos 100 pedidos processados.

Embora o aumento no volume de dados seja uma consequência natural do padrão, diversas técnicas podem ser empregadas para mitigar esse impacto. Uma das mais utilizadas é o Snapshotting, que consiste em armazenar periodicamente o estado consolidado de uma entidade, reduzindo a quantidade de eventos necessários para sua reconstrução. Outras estratégias incluem compactação, arquivamento de eventos antigos e particionamento do armazenamento de eventos.

# Experimento 4: Latência

## Resultados

Os resultados foram:

- 100 reqs, 1 VU: avg=75.95ms min=58.66ms med=74.98ms max=94.81ms p(90)=89.49ms p(95)=90.9ms; throughput = 13.124283 req/s
- 100 reqs, 5 VUs: avg=93.51ms min=59ms med=88.71ms max=177.45ms p(90)=118.98ms p(95)=123.53ms; throughput = 52.143256 req/s

Na implementação sem padrões, os resultados observados foram os seguintes:

- 100 reqs, 1 VU: avg=36.32ms min=27.66ms med=35.65ms max=77.03ms p(90)=39.34ms p(95)=40.88ms; throughput = 27.354096 req/s
- 100 reqs, 5 VUs: avg=44.68ms min=32.36ms med=43.21ms max=104.46ms p(90)=48.13ms p(95)=51.07ms; throughput = 109.396254 req/s

## Análise

Os resultados indicam um aumento consistente da latência em comparação à implementação distribuída sem padrões. No cenário com 1 usuário virtual, a latência média aumentou de 36,32 ms para 75,95 ms, representando um crescimento aproximado de 109%. No cenário com 5 usuários virtuais, a latência média passou de 44,68 ms para 93,51 ms, correspondendo a um aumento de aproximadamente 109%.

Da mesma forma, observou-se redução do throughput em ambos os cenários. Com 1 usuário virtual, a taxa de processamento caiu de 27,35 para 13,12 requisições por segundo. Com 5 usuários virtuais, a taxa reduziu de 109,39 para 52,14 requisições por segundo.

Esse comportamento era esperado, uma vez que a implementação baseada em Event Sourcing passa a registrar múltiplos eventos para representar uma única operação de negócio. Enquanto a abordagem tradicional persiste apenas o estado final das entidades, o Event Sourcing registra cada transição de estado individualmente, aumentando a quantidade de operações de escrita realizadas durante o processamento do pedido.

Apesar do impacto negativo sobre a latência e o throughput, os resultados devem ser analisados em conjunto com os benefícios obtidos em termos de auditoria, rastreabilidade e capacidade de reconstrução do estado da aplicação, características que não estão presentes na implementação distribuída base.

# Conclusão Parcial

Os experimentos realizados demonstraram que o Event Sourcing oferece benefícios significativos relacionados à auditoria, rastreabilidade e recuperação de estado, permitindo que todo o histórico de alterações seja preservado de forma explícita e permanente.

Em contrapartida, a abordagem introduz custos adicionais de armazenamento e processamento, uma vez que múltiplos eventos precisam ser persistidos para representar uma única operação de negócio. Esse aumento refletiu diretamente nos resultados de latência e throughput observados durante os experimentos.

Dessa forma, os resultados indicam que o Event Sourcing não deve ser encarado como um substituto universal para os modelos tradicionais de persistência, mas como uma estratégia especializada para cenários em que histórico completo, auditoria e reconstrução de estado são requisitos relevantes. Em aplicações onde tais características não são prioritárias, a complexidade adicional introduzida pelo padrão pode não justificar sua adoção.
