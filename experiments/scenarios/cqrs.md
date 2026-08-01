A implementação desenvolvida adota os princípios fundamentais de CQRS e Event Sourcing, porém com adaptações voltadas à simplificação do ambiente experimental. Os eventos são persistidos como fonte histórica das alterações realizadas em cada domínio e utilizados para alimentar projeções de leitura atualizadas de forma assíncrona por meio de mensagens publicadas no RabbitMQ.

Diferentemente de implementações ortodoxas de Event Sourcing, nas quais o estado atual é frequentemente reconstruído a partir da reaplicação dos eventos de um agregado, a solução proposta utiliza projeções materializadas como fonte principal para consultas e para parte das validações operacionais. Dessa forma, o Event Sourcing é empregado principalmente como mecanismo de auditoria, rastreabilidade, recuperação de estado e sincronização entre serviços, reduzindo a necessidade de reconstrução frequente de agregados em memória.

Essa abordagem representa um compromisso entre a pureza conceitual do padrão e a simplicidade necessária para o ambiente experimental adotado no trabalho.

# Experimento 1: Auditoria e Rastreabilidade

## Objetivo

Verificar a capacidade do sistema de preservar o histório completo das alterações realizadas durante uma transação distribuída.

## Resultados

Os resultados observados permaneceram equivalentes aos obtidos na implementação anterior baseada apenas em Event Sourcing. Isso ocorre porque a introdução do CQRS não altera a persistência dos eventos, que continuam sendo registrados como fonte histórica das alterações realizadas no sistema. Dessa forma, a capacidade de auditoria e rastreabilidade permanece preservada.

# Experimento 2: Recuperação de Estado

## Objetivo

Verificar se o estado do sistema pode ser reconstruído a partir dos eventos persistidos.

## Resultados

A capacidade de reconstrução do estado também permaneceu inalterada. Como os eventos continuam sendo armazenados de forma permanente, foi possível reconstruir as projeções a partir do histórico persistido. A introdução do CQRS modifica apenas a forma como as consultas são realizadas, sem impactar o mecanismo de replay dos eventos.

# Experimento 3: Impacto no Armazenamento

## Objetivo

Comparar a quantidade de dados persistidos entre a abordagem tradicional e Event Sourcing.

## Resultados

O impacto no armazenamento permaneceu equivalente ao observado na implementação anterior. Como o CQRS não altera a quantidade de eventos produzidos durante o processamento das operações, o volume de dados persistidos continua sendo determinado principalmente pela estratégia de Event Sourcing adotada.

# Experimento 4: Latência

## Resultados

Os resultados foram:

- 100 reqs, 1 VU: avg=122.33ms min=113.63ms med=122.02ms max=135.97ms p(90)=126.89ms p(95)=131.99ms; throughput = 8.152911 req/s
- 100 reqs, 5 VUs: avg=188.56ms min=121.14ms med=182.88ms max=273.71ms p(90)=240.2ms p(95)=264.09ms; throughput = 26.178474 req/s

Na implementação com Event Sourcing apenas, os resultados observados foram os seguintes:

- 100 reqs, 1 VU: avg=75.95ms min=58.66ms med=74.98ms max=94.81ms p(90)=89.49ms p(95)=90.9ms; throughput = 13.124283 req/s
- 100 reqs, 5 VUs: avg=93.51ms min=59ms med=88.71ms max=177.45ms p(90)=118.98ms p(95)=123.53ms; throughput = 52.143256 req/s

## Análise

A introdução do CQRS resultou em aumento da latência e redução do throughput em comparação à implementação baseada apenas em Event Sourcing. Esse comportamento era esperado, uma vez que o processamento passou a envolver não apenas a persistência dos eventos, mas também a atualização assíncrona das projeções de leitura e a coordenação adicional necessária para manter os modelos de escrita e leitura sincronizados.

Embora haja impacto no desempenho das operações de escrita, a separação entre comandos e consultas permite a construção de modelos de leitura especializados, favorecendo cenários em que a carga de consultas é significativamente superior à carga de escrita.

# Experimento 5: Consistência Eventual

## Resultados

63 ms Lag da projeção CQRS
485 ms Tempo para convergência completa da transação distribuída

## Análise

Os resultados evidenciam a presença de consistência eventual no sistema. Após a gravação do evento inicial, a projeção de leitura foi atualizada em aproximadamente 63 ms, demonstrando que o atraso introduzido pelo mecanismo assíncrono permaneceu relativamente baixo.

Entretanto, a convergência completa da transação distribuída, incluindo estoque, pagamento e envio, ocorreu em aproximadamente 485 ms. Esse valor representa o tempo necessário para que todos os serviços participantes processem suas respectivas etapas e propaguem os eventos subsequentes.

Os resultados demonstram o principal trade-off introduzido pelo CQRS: em vez de consistência imediata, o sistema passa a operar sob consistência eventual. Em contrapartida, obtém-se maior desacoplamento entre escrita e leitura, além da possibilidade de utilizar modelos de consulta especializados.

# Conclusão Parcial

A integração entre CQRS e Event Sourcing manteve os benefícios já observados de auditoria, rastreabilidade e reconstrução de estado, ao mesmo tempo em que introduziu um modelo de leitura desacoplado do fluxo de escrita.

Os experimentos mostraram que essa separação tem custo operacional mensurável, refletido no aumento da latência e na redução do throughput das operações de escrita. Além disso, observou-se a introdução de consistência eventual, característica inerente à sincronização assíncrona entre eventos e projeções.

Apesar desses custos, a arquitetura resultante oferece maior flexibilidade para consultas, melhor capacidade de auditoria e possibilidade de recuperação do estado da aplicação a partir do histórico de eventos. Os resultados reforçam que a adoção de CQRS e Event Sourcing representa um trade-off entre desempenho operacional e capacidades avançadas de gestão de dados distribuídos.
