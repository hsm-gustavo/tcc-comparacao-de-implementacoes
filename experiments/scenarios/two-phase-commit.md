# Análise de Caso Distribuído com 2PC

## Introdução

Este documento apresenta a implementação distribuída da aplicação de e-commerce após a adoção do padrão Two-Phase Commit (2PC). O objetivo desta versão é servir como cenário experimental para avaliar os efeitos da coordenação distribuída sobre atomicidade, latência transacional e integridade referencial.

A partir dessa implementação, foram realizados experimentos voltados à observação de três aspectos principais:

- Atomicidade distribuída com coordenação 2PC
- Latência transacional
- Integridade referencial

Os resultados obtidos serão comparados com a versão distribuída sem padrões de coordenação, permitindo analisar o impacto do 2PC na confiabilidade transacional do sistema.

# Ambiente Experimental

Os experimentos foram executados localmente em uma única máquina utilizando contêineres Docker orquestrados por meio do Docker Compose.

## Configuração do ambiente

- Linguagem e framework: TypeScript com NestJS
- Banco de dados: PostgreSQL
- Arquitetura: microsserviços independentes
- Coordenação entre serviços: Two-Phase Commit (2PC) via HTTP/REST
- Orquestração local: Docker Compose

## Hardware utilizado

- Processador: Intel Core i7-3770
- Memória RAM: 16 GB
- Sistema operacional: Linux

Todos os serviços e bancos de dados foram executados localmente na mesma máquina, permitindo um ambiente controlado para comparação arquitetural.

Os resultados obtidos não representam medições absolutas de desempenho em ambientes distribuídos reais, mas sim uma análise comparativa do impacto introduzido pela coordenação distribuída e pela decomposição arquitetural.

# Arquitetura Base Distribuída

A implementação foi construída a partir da refatoração da versão distribuída anterior da aplicação de e-commerce para uma arquitetura coordenada por 2PC.

O sistema continua dividido em quatro serviços principais:

- Billing Service
- Order Service
- Warehouse Service
- Shipping Service

Cada serviço permanece responsável por um domínio específico da aplicação, com sua própria lógica e persistência de dados. A principal diferença desta versão está na introdução de uma coordenação explícita entre os serviços para que a operação distribuída siga fases de preparação e confirmação antes da conclusão definitiva do pedido.

Enquanto a implementação sem padrões dependia apenas de chamadas independentes entre serviços, a versão com 2PC introduz um protocolo de duas fases para reduzir inconsistências causadas por falhas parciais. Com isso, a aplicação passa a coordenar operações de commit e abort de maneira mais sistemática, aproximando o comportamento distribuído de uma transação atômica global, embora sem atingir integralmente as mesmas garantias de um sistema centralizado.

# Experimentos

## Atomicidade Distribuída com 2PC

### Objetivo

Avaliar o comportamento do sistema diante de falhas parciais em uma operação distribuída coordenada por 2PC.

### Procedimento 1

Foi introduzida uma falha proposital na fase de preparação do Billing Service, executado após a reserva de estoque realizada pelo Warehouse Service e antes da etapa de envio conduzida pelo Shipping Service.

Uma requisição foi enviada ao endpoint de criação de pedidos com os seguintes dados:

```json
{
  "customerId": "cust-123",
  "productId": "bd6ab524-8202-4df8-be4d-b52a61eefea6",
  "quantity": 1,
  "amount": 249.9
}
```

### Resultados 1

Após a execução da operação, observou-se que:

- o pedido foi cancelado;
- nenhuma operação de pagamento foi efetivada;
- o estoque não foi decrementado.

### Procedimento 2

Foi introduzida uma falha proposital na fase de confirmação do Billing Service, executado após a reserva de estoque realizada pelo Warehouse Service e antes da etapa de envio conduzida pelo Shipping Service.

### Resultados 2

Após a execução da operação, observou-se que:

- o pedido foi cancelado;
- o pagamento foi marcado como falho;
- o estoque foi decrementado.

### Análise

No primeiro cenário, a falha ocorreu ainda durante a fase de preparação da transação distribuída. Como consequência, os participantes não confirmaram suas operações localmente, permitindo que a transação fosse abortada antes da efetivação das alterações persistentes. Esse comportamento evidencia a principal característica do protocolo 2PC: a coordenação prévia entre os participantes antes da confirmação definitiva da operação distribuída.

Em comparação com a implementação sem coordenação explícita, observou-se uma melhora significativa na manutenção da atomicidade distribuída, reduzindo situações nas quais apenas parte dos serviços conclui suas operações com sucesso.

Entretanto, o segundo cenário evidencia uma limitação inerente ao protocolo. Embora o 2PC reduza inconsistências durante a fase de preparação, falhas ocorridas após o início da fase de confirmação ainda podem resultar em estados parcialmente confirmados entre os participantes.

Isso ocorre porque, após o envio da decisão de commit, alguns serviços podem concluir suas operações enquanto outros falham antes da finalização completa da transação distribuída. Como consequência, parte do estado do sistema pode permanecer efetivado mesmo diante de falhas subsequentes.

Dessa forma, o protocolo 2PC melhora significativamente a atomicidade distribuída em comparação à implementação sem coordenação, mas não elimina completamente a possibilidade de inconsistências em cenários de falha durante a fase de confirmação.

Além disso, a necessidade de sincronização entre todos os participantes aumenta o acoplamento temporal entre os serviços, tornando a operação distribuída dependente da disponibilidade e tempo de resposta de todos os participantes envolvidos.

## Latência Transacional com 2PC

### Objetivo

Avaliar o impacto da coordenação 2PC sobre o tempo de resposta das operações distribuídas.

### Procedimento

Foram realizadas 15 requisições consecutivas ao endpoint de criação de pedidos, utilizando o mesmo payload e sob as mesmas condições de execução.

A métrica utilizada foi o tempo total da transação (time_total), obtido por meio da ferramenta curl.

### Resultados

Os resultados foram:

- 100 reqs, 1 VU: avg=57.31ms min=47.89ms med=55.63ms max=97.35ms p(90)=63.72ms p(95)=69.47ms; throughput = 17.387899 req/s
- 100 reqs, 5 VUs: avg=82.15ms min=59.56ms med=75.55ms max=194.75ms p(90)=97.2ms p(95)=123.03ms; throughput = 59.879348 req/s

Na implementação sem padrões, os resultados observados foram os seguintes::

- 100 reqs, 1 VU: avg=36.32ms min=27.66ms med=35.65ms max=77.03ms p(90)=39.34ms p(95)=40.88ms; throughput = 27.354096 req/s
- 100 reqs, 5 VUs: avg=44.68ms min=32.36ms med=43.21ms max=104.46ms p(90)=48.13ms p(95)=51.07ms; throughput = 109.396254 req/s

### Análise

Os resultados obtidos demonstram o impacto introduzido pela coordenação distribuída baseada em Two-Phase Commit (2PC) sobre o desempenho da aplicação.

No cenário com 1 usuário virtual (1 VU), a implementação com 2PC apresentou tempo médio de resposta de 57.31 ms, enquanto a implementação distribuída sem mecanismos de coordenação apresentou média de 36.32 ms. Isso representa um aumento aproximado de 57.8% na latência média da operação.

Os percentis superiores também evidenciam aumento na variabilidade temporal das requisições. O percentil 95 (p95) passou de 40.88 ms na implementação sem padrões para 69.47 ms com 2PC, indicando que as requisições mais lentas sofrem impacto ainda mais significativo devido às etapas adicionais de sincronização.

No cenário com concorrência leve (5 VUs), o impacto tornou-se ainda mais evidente. A média de latência aumentou de 44.68 ms para 82.15 ms, representando um crescimento aproximado de 83.8%. O percentil 95 também apresentou aumento expressivo, passando de 51.07 ms para 123.03 ms.

O throughput da aplicação também foi reduzido após a introdução do protocolo. Na execução com 1 VU, a taxa de processamento caiu de aproximadamente 27.35 requisições por segundo para 17.38 requisições por segundo. Sob concorrência leve, o throughput reduziu de aproximadamente 109.39 req/s para 59.87 req/s.

Esse comportamento está diretamente relacionado à necessidade de coordenação adicional introduzida pelo protocolo 2PC. Diferentemente da implementação sem coordenação explícita, cada transação distribuída passa a exigir fases adicionais de preparação e confirmação, aumentando o número de comunicações necessárias entre os participantes.

Além disso, o protocolo introduz dependência temporal entre os serviços, uma vez que a conclusão da transação depende da resposta de todos os participantes antes da decisão final do coordenador. Como consequência, o tempo total da operação tende a ser limitado pelo participante mais lento da transação distribuída.

Outro aspecto observado é o aumento da dispersão temporal das requisições, evidenciado pelos maiores valores máximos e percentis superiores. Esse comportamento sugere menor previsibilidade temporal da aplicação sob coordenação distribuída, especialmente em cenários concorrentes.

Embora o ambiente experimental tenha sido executado localmente, sem latência real de rede entre máquinas físicas distintas, os resultados demonstram que o overhead do protocolo já é perceptível mesmo em ambientes controlados. Em ambientes distribuídos reais, esse impacto tende a ser ainda mais significativo devido a fatores como latência de rede, retransmissões e falhas parciais.

Apesar do aumento de latência e da redução de throughput, os experimentos anteriores demonstraram que o 2PC foi capaz de eliminar inconsistências observadas na fase de preparação da transação, fornecendo maior garantia de atomicidade distribuída. Dessa forma, os resultados evidenciam o principal trade-off do protocolo: o aumento da consistência distribuída ocorre às custas de maior latência, menor throughput e maior dependência entre os participantes da transação.

## Integridade Referencial com 2PC

O protocolo 2PC atua exclusivamente na coordenação de transações distribuídas, não oferecendo mecanismos nativos para garantia de integridade referencial entre serviços independentes.

Assim, a adoção do 2PC não altera o comportamento observado na implementação distribuída sem padrões de coordenação. Cada serviço continua responsável por sua própria persistência de dados, sem suporte a restrições relacionais globais equivalentes às chaves estrangeiras presentes em bancos relacionais centralizados.

Como consequência, ainda é possível a existência de referências órfãs entre serviços. Por exemplo, pedidos podem continuar armazenando identificadores de produtos removidos do serviço de estoque, caracterizando inconsistências referenciais distribuídas.

# Conclusão Parcial

Os experimentos realizados demonstram que a adoção do protocolo Two-Phase Commit (2PC) melhora significativamente a coordenação de transações distribuídas em comparação à implementação sem padrões de coordenação explícita.

Os resultados observados evidenciam que o protocolo reduz inconsistências durante a fase de preparação da transação, permitindo maior controle sobre falhas parciais e aproximando o comportamento distribuído de uma operação atomicamente coordenada.

Entretanto, os testes também demonstram que o 2PC não elimina completamente inconsistências em cenários de falha durante a fase de confirmação. Além disso, a necessidade de sincronização entre todos os participantes introduz aumento perceptível de latência e maior dependência temporal entre os serviços envolvidos.

Outro aspecto relevante observado é que o protocolo não resolve problemas relacionados à integridade referencial distribuída, uma vez que cada serviço continua responsável por sua própria persistência de dados e não existem restrições relacionais globais equivalentes às presentes em bancos de dados centralizados.

Dessa forma, os experimentos evidenciam que o 2PC representa uma solução voltada principalmente à coordenação da atomicidade distribuída, oferecendo maior consistência em comparação à implementação sem coordenação, porém ao custo de maior latência, maior acoplamento temporal e limitações relacionadas à disponibilidade e escalabilidade.

Esses resultados reforçam os trade-offs inerentes às arquiteturas distribuídas, demonstrando que o aumento das garantias transacionais frequentemente implica maior complexidade operacional e redução da flexibilidade do sistema.
