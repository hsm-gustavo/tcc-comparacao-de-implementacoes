# Análise de Caso Distribuído

## Introdução

Este documento apresenta a implementação base distribuída de uma aplicação de e-commerce desenvolvida utilizando arquitetura de microsserviços. O objetivo desta implementação é servir como cenário experimental para a análise dos impactos introduzidos pela decomposição de um sistema monolítico em serviços independentes.

A partir dessa implementação, foram realizados experimentos voltados à observação de três aspectos principais:

- Atomicidade distribuída
- Latência transacional
- Integridade referencial

Os resultados obtidos servirão como base comparativa para avaliação posterior de padrões arquiteturais voltados à coordenação distribuída e confiabilidade transacional.

# Ambiente Experimental

Os experimentos foram executados localmente em uma única máquina utilizando contêineres Docker orquestrados por meio do Docker Compose.

## Configuração do ambiente

- Linguagem e framework: TypeScript com NestJS
- Banco de dados: PostgreSQL
- Arquitetura: microsserviços independentes
- Comunicação entre serviços: HTTP/REST
- Orquestração local: Docker Compose

## Hardware utilizado

- Processador: Intel Core i7-3770
- Memória RAM: 16 GB
- Sistema operacional: Linux

Todos os serviços e bancos de dados foram executados localmente na mesma máquina, permitindo um ambiente controlado para comparação arquitetural.

Os resultados obtidos não representam medições absolutas de desempenho em ambientes distribuídos reais, mas sim uma análise comparativa do impacto introduzido pela comunicação distribuída e pela decomposição arquitetural.

# Arquitetura Base Distribuída

A implementação foi construída a partir da refatoração da versão monolítica do sistema de e-commerce para uma arquitetura baseada em microsserviços.

O sistema foi dividido em quatro serviços principais:

- Billing Service
- Order Service
- Warehouse Service
- Shipping Service

Cada serviço tornou-se responsável por um domínio específico da aplicação, possuindo sua própria lógica e persistência de dados.

Enquanto a versão monolítica utilizava um banco de dados centralizado e transações locais para garantir a consistência, a versão distribuída passa a depender de comunicação remota entre serviços. Como consequência, garantias tradicionalmente fornecidas por bancos relacionais, como atomicidade global e integridade referencial, deixam de existir de forma automática.

# Experimentos

## Atomicidade Distribuída

### Objetivo

Avaliar o comportamento do sistema diante de falhas parciais em uma operação distribuída.

### Procedimento

Foi introduzida uma falha proposital no Billing Service, executado após a reserva de estoque realizada pelo Warehouse Service e antes da etapa de envio conduzida pelo Shipping Service.

Uma requisição foi enviada ao endpoint de criação de pedidos utilizando o seguinte payload:

```json
{
  "customerId": "cust-123",
  "productId": "d4fbd02e-f2bf-46ec-a9b1-dc0abf696639",
  "quantity": 3,
  "amount": 448.5
}
```

### Resultados

Após a execução da operação, observou-se que:

- o pedido foi cancelado
- o pagamento foi marcado como falho
- o estoque permaneceu decrementado

O produto possuía inicialmente 27 unidades em estoque. Após a falha da operação, a quantidade passou para 24 unidades, mesmo sem conclusão bem-sucedida da transação global.

### Análise

O experimento demonstra que transações locais independentes não garantem consistência global em arquiteturas distribuídas.

Embora o pedido tenha sido cancelado corretamente, o estado do estoque permaneceu alterado, evidenciando a ausência de mecanismos automáticos de rollback distribuído.

Esse cenário caracteriza uma inconsistência entre serviços causada por falha parcial da operação distribuída.

## Latência Transacional

### Objetivo

Avaliar o impacto da comunicação distribuída sobre o tempo de resposta das operações

### Procedimento

Foram realizadas 100 requisições consecutivas ao endpoint de criação de pedidos, utilizando o mesmo payload e sob as mesmas condições de execução.

As métricas utilizadas foram o tempo total da transação (time_total), obtido por meio da ferramenta k6.

### Resultados

Os resultados foram:

- 100 reqs, 1 VU: avg=36.32ms min=27.66ms med=35.65ms max=77.03ms p(90)=39.34ms p(95)=40.88ms; throughput = 27.354096 req/s
- 100 reqs, 5 VUs: avg=44.68ms min=32.36ms med=43.21ms max=104.46ms p(90)=48.13ms p(95)=51.07ms; throughput = 109.396254 req/s

Na implementação monolítica equivalente, os resultados observados foram os seguintes:

- 100 reqs, 1 VU: avg=15.4ms min=8.33ms med=15.67ms max=25.87ms p(90)=17.76ms p(95)=18.27ms; throughput = 63.909243 req/s
- 100 reqs, 5 VUs: avg=30.86ms min=9.62ms med=26.91ms max=107.55ms p(90)=53ms p(95)=61.92ms; throughput = 157.547626 req/s

### Análise

Os resultados obtidos evidenciam o impacto introduzido pela decomposição arquitetural em microsserviços, mesmo na ausência de mecanismos adicionais de coordenação distribuída.

No cenário com 1 usuário virtual (1 VU), a implementação distribuída apresentou tempo médio de resposta de 36.32ms, enquanto a implementação monolítica obteve média de 15.4ms. Isso representa um aumento aproximado de 135.8% na latência média da operação.

Os percentis também indicam maior variabilidade temporal na arquitetura distribuída. O percentil 95 (p95) passou de 18.27ms no monolíto para 40.88ms na versão distribuída, demonstrando que as requisições mais lentas sofrem impacto significativo da comunicação entre serviços.

Esse aumento era esperado, uma vez que a arquitetura distribuída introduz custos adicionais relacionados à serialização de dados, comunicação HTTP, sincronização entre serviços e múltiplas operações de entrada e saída (I/O). Diferentemente do monólito, no qual as chamadas ocorrem no mesmo processo e espaço de memória, a versão distribuída depende de interações remotas entre processos independentes.

No cenário com concorrência leve (5 VUs), a diferença de latência media permaneceu perceptível, embora menos acentuada proporcionalmente. A implementação dsitribuída apresentou média de 44.68ms, enquanto o monólito registrou 30.86ms, representando um aumento aproximado de 44.8%.

Os resultados de throughput indicam comportamento semelhante entre as arquiteturas sob carga concorrente. O monólito alcançou aproximadamente 157.5 requisições por segundo, enquanto a versão distribuída atingiu cerca de 109.4 requisições por segundo. Embora inferior, o throughput da arquitetura distribuída permaneceu relativamente próximo, sugerindo que o principal impacto da decomposição arquitetural ocorre na latência individual das operações, e não necessariamente na capacidade geral de processamento em cenários de baixa concorrência.

Além disso, observa-se um aumento na dispersão temporal das requisições na arquitetura distribuída, evidenciado pelos maiores valores máximos e percentis superiores. Isso ocorre devido à maior quantidade de pontos de falha e sincronização existentes entre os serviços, tornando o comportamento temporal menos previsível em comparação ao monólito.

De forma geral, os resultados demonstram que a migração para microsserviços implica um custo mensurável de desempenho, mesmo antes da introdução de padrões adicionais de coordenação distribuída. Em contrapartida, essa abordagem oferece benefícios arquiteturais relacionados à modularidade, independência de implantação e escalabilidade organizacional, evidenciando o trade-off entre desempenho e flexibilidade arquitetural.

## Integridade Referencial

### Objetivo

Avaliar a manutenção da integridade referencial em um ambiente distribuído sem chaves estrangeiras globais.

### Procedimento

Foi removido manualmente do Warehouse Service o produto identificado pelo ID:

d4fbd02e-f2bf-46ec-a9b1-dc0abf696639

O sistema possuía registros de pedidos que ainda referenciavam esse produto.

### Resultados

Após a remoção, os registros associados ao produto permaneceram armazenados no serviço de pedidos, mesmo sem a existência do recurso correspondente no serviço de estoque.

### Análise

O experimento evidencia a perda das garantias automáticas de integridade referencial tradicionalmente fornecidas por bancos de dados relacionais centralizados.

Em arquiteturas distribuídas, referências entre entidades pertencentes a serviços distintos tornam-se dependentes de validações e mecanismos implementados na camada da aplicação.

Esse cenário caracteriza uma incosistência lógica entre serviços, frequentemente associada ao surgimento de referências órfãs em sistemas distribuídos.

# Conclusão Parcial

Os experimentos realizados evidenciam alguns dos principais desafios introduzidos pela adoção de arquiteturas distribuídas baseadas em microsserviços.

Os resultados demonstram que a decomposição de um sistema monolítico em serviços independentes implica perda de garantias tradicionalmente fornecidas por transações locais e bancos de dados centralizados, especialmente no que se refere à atomicidade global, integridade referencial e previsibilidade de latência.

Esses problemas motivam a adoção de padrões específicos para coordenação distribuída, confiabilidade de comunicação e consistência eventual, os quais serão analisados em experimentos posteriores.
