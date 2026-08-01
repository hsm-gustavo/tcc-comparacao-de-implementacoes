# Atomicidade Distribuída com Saga Coreografada

## Objetivo

Avaliar o comportamento do sistema diante de falhas parciais em operações distribuídas coordenadas pelo padrão Saga, com coreografia baseada em eventos publicados em RabbitMQ.

Diferente de abordagens centralizadas, a Saga coreografada distribui a responsabilidade do fluxo entre os serviços participantes. Cada serviço reage a eventos, executa sua parte da transação e publica o próximo evento da cadeia ou um evento de falha para disparar compensações.

Nesta implementação, o Order Service atua como ponto de entrada e consolidação do estado final do pedido, enquanto Warehouse, Billing e Shipping executam a coreografia por eventos.

## Estrutura da Saga

O fluxo principal da saga foi implementado da seguinte forma:

- o Order Service recebe a criação do pedido e publica o evento inicial `order.created`,
- o Warehouse Service reserva o estoque e publica `warehouse.stock_reserved`,
- o Billing Service processa o pagamento e publica `billing.payment_confirmed`,
- o Shipping Service cria o envio e publica `shipping.shipment_created`,
- o Order Service consome o evento final e marca o pedido como confirmado.

Quando ocorre uma falha em qualquer etapa, o serviço responsável publica um evento de falha e os participantes anteriores executam suas compensações locais.

## Procedimento 1: Falha no Billing Service

Foi introduzida uma falha proposital no Billing Service durante o processamento do pagamento, após a reserva de estoque realizada pelo Warehouse Service.

## Resultados 1

Após a execução da operação, observou-se que:

- o pedido por alguns milissegundos foi marcado como pendente, mas então foi cancelado,
- o estoque reservado foi liberado,
- o pagamento não foi efetivado,
- o envio não foi criado.

## Análise 1

A falha no Billing Service demonstra o comportamento esperado de uma Saga coreografada em condições normais de compensação. Embora a reserva de estoque tenha sido realizada com sucesso, a falha no processamento do pagamento gerou um evento de erro que desencadeou a execução das ações compensatórias necessárias para restaurar a consistência do sistema.

Observou-se que o pedido permaneceu temporariamente em estado pendente antes de ser cancelado. Esse comportamento é característico de sistemas baseados em consistência eventual, nos quais a convergência para o estado final depende da propagação e processamento dos eventos entre os serviços participantes.

Diferente do 2PC, não houve necessidade de bloqueio ou espera por uma decisão global antes da execução das operações locais. Cada serviço executou sua responsabilidade de forma autônoma e reagiu aos eventos recebidos. Como resultado, a consistência global foi restaurada por meio de compensações, e não pela prevenção da execução das operações.

Esse experimento demonstra que a Saga é capaz de manter a consistência do processo de negócio mesmo diante de falhas parciais, desde que todas as ações compensatórias sejam executadas corretamente.

## Procedimento 2: Falha na compensação

Foi introduzida uma falha proposital no Billing Service e posteriormente uma falha adicional durante a compensação executada pelo Warehouse Service.

## Resultados 2

Após a execução da operação, observou-se que:

- o pedido foi cancelado,
- o estoque reservado continuou reservado,
- o pagamento não foi efetivado,
- o envio não foi criado.

## Análise 2

A falha introduzida durante a compensação evidencia uma das principais limitações do padrão Saga. Embora a falha original no pagamento tenha sido detectada corretamente e o pedido tenha sido cancelado, a impossibilidade de executar a compensação do estoque impediu o retorno completo do sistema ao estado anterior à transação.

Como consequência, a reserva permaneceu registrada mesmo após o cancelamento do pedido, produzindo uma inconsistência operacional. Esse resultado demonstra que a confiabilidade da Saga depende não apenas da execução das operações principais, mas também da disponibilidade e correto funcionamento das operações compensatórias.

Enquanto protocolos como o 2PC procuram evitar estados intermediários por meio de coordenação e sincronização explícitas, a Saga aceita a existência desses estados temporários e delega sua correção às compensações posteriores. Quando uma dessas compensações falha, o sistema pode permanecer inconsistente até que mecanismos adicionais de recuperação sejam executados.

Esse cenário reforça a necessidade de estratégias complementares, como repetição automática de compensações, filas de mensagens persistentes, políticas de retry e monitoramento operacional, para aumentar a robustez de implementações baseadas em Saga.

## Observações de Implementação

O laboratório foi estruturado com as seguintes decisões:

- RabbitMQ foi usado como barramento de eventos,
- as filas foram configuradas como duráveis,
- os consumidores operam com ack explícito,
- o Order Service consolida o estado final do pedido,
- Warehouse mantém um ledger de reservas para suportar compensações de estoque,
- Billing e Shipping publicam eventos de sucesso e falha para encadear o fluxo.

Esse desenho mantém o modelo de saga coreografada sem um orquestrador central de mensagens de negócio, embora o Order Service continue sendo o ponto de entrada e de fechamento do ciclo do pedido.

# Latência Transacional com Saga

## Objetivo

Avaliar o impacto do padrão Saga sobre o tempo de resposta das operações distribuídas.

## Procedimento

Foram realizadas 100 requisições ao endpoint de criação de pedidos utilizando k6, considerando cenários com 1 usuário virtual (1 VU) e concorrência leve com 5 usuários virtuais (5 VUs).

As métricas observadas incluíram:

- latência média,
- mediana,
- percentis (p90 e p95),
- throughput.

## Resultados

Os resultados observados foram:

- 100 reqs, 1 VU: avg=73.48ms min=59.38ms med=74.4ms max=94.95ms p(90)=81.22ms p(95)=82.36ms; throughput = 13.562725 req/s
- 100 reqs, 5 VUs: avg=112.29ms min=67.15ms med=104.89ms max=261.38ms p(90)=129.68ms p(95)=176.53ms; throughput = 43.686941 req/s

Na implementação distribuída sem padrões, os resultados observados foram:

- 100 reqs, 1 VU: avg=36.32ms min=27.66ms med=35.65ms max=77.03ms p(90)=39.34ms p(95)=40.88ms; throughput = 27.354096 req/s
- 100 reqs, 5 VUs: avg=44.68ms min=32.36ms med=43.21ms max=104.46ms p(90)=48.13ms p(95)=51.07ms; throughput = 109.396254 req/s

## Análise

Os resultados indicam que a adoção do padrão Saga introduz um overhead adicional em relação à implementação distribuída sem mecanismos de coordenação. No cenário com 1 usuário virtual, a latência média aumentou de 36,32 ms para 73,48 ms, enquanto no cenário com 5 usuários virtuais o valor passou de 44,68 ms para 112,29 ms.

Esse aumento está associado principalmente à comunicação assíncrona baseada em eventos. Diferente da implementação sem padrões, onde o fluxo ocorre por chamadas diretas entre serviços, a Saga exige a publicação, persistência, roteamento e consumo de mensagens intermediárias para coordenar cada etapa do processo de negócio.

Observa-se também uma redução significativa do throughput. Com 1 usuário virtual, a capacidade de processamento caiu de aproximadamente 27,35 requisições por segundo para 13,56 requisições por segundo. Em 5 usuários virtuais, o throughput reduziu de 109,39 para 43,69 requisições por segundo. Esses resultados refletem o custo operacional introduzido pelo broker de mensagens e pela necessidade de processamento dos eventos de coordenação.

Os percentis p90 e p95 também apresentaram crescimento considerável, indicando que não apenas a média aumentou, mas também a dispersão dos tempos de resposta. Esse comportamento é esperado em arquiteturas orientadas a eventos, nas quais atrasos na fila, processamento assíncrono e sincronização entre consumidores podem ampliar a variabilidade da latência observada.

Apesar desse impacto no desempenho, a Saga oferece benefícios importantes em termos de desacoplamento e tolerância a falhas. Diferentemente do 2PC, não existe um coordenador central responsável por bloquear participantes até a decisão final da transação. Cada serviço permanece autônomo e pode evoluir independentemente, característica frequentemente associada a arquiteturas de microsserviços em larga escala.

# Conclusão Parcial

Os experimentos demonstraram que o padrão Saga é capaz de restaurar a consistência do processo de negócio por meio de mecanismos de compensação, mesmo quando ocorrem falhas durante a execução da transação distribuída. Em cenários onde as compensações foram executadas corretamente, o sistema convergiu para um estado consistente sem necessidade de bloqueios distribuídos ou coordenação centralizada.

Entretanto, os testes também evidenciaram que a consistência obtida pela Saga é condicionada ao sucesso das operações compensatórias. Quando uma compensação falha, inconsistências podem permanecer no sistema, exigindo mecanismos adicionais de recuperação e monitoramento.

Sob a perspectiva de desempenho, observou-se aumento da latência e redução do throughput em comparação à implementação distribuída sem padrões de coordenação. Ainda assim, o padrão elimina a necessidade de sincronização rígida entre os participantes e favorece o desacoplamento entre serviços.

Dessa forma, a Saga representa uma alternativa adequada para sistemas distribuídos que priorizam disponibilidade, autonomia dos serviços e consistência eventual, aceitando como contrapartida maior complexidade operacional e dependência de mecanismos de compensação para manutenção da integridade do processo de negócio.
