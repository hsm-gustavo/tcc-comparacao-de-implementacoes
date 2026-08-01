# Atomicidade Distribuída com Transactional Outbox

A introdução do Transactional Outbox não altera o comportamento funcional da Saga coreografada em relação à atomicidade observada nos testes anteriores. Os mesmos mecanismos de compensação continuam sendo responsáveis por restaurar a consistência quando ocorre uma falha durante a execução da transação distribuída.

A principal diferença está na forma como os eventos são produzidos. Em vez de serem publicados diretamente para o broker, os eventos passam a ser persistidos em uma tabela de outbox dentro da mesma transação local que modifica os dados de negócio. Posteriormente, um worker assíncrono é responsável por publicar esses eventos para o RabbitMQ.

Dessa forma, o padrão não modifica o resultado final das operações da Saga, mas adiciona garantias relacionadas à confiabilidade da publicação dos eventos.

# Latência Transacional com Saga

Diferente dos experimentos anteriores, a latência observada na requisição HTTP deixa de representar o tempo total de processamento da operação distribuída. Com a introdução do Transactional Outbox, a responsabilidade pela publicação dos eventos passa a ser delegada a um processo assíncrono (worker) executado em segundo plano. Como consequência, o tempo de resposta ao cliente é reduzido, porém parte significativa do processamento ocorre após o retorno da requisição. Dessa forma, a comparação direta da latência HTTP com as implementações anteriores não representa adequadamente o custo total da operação.

## Resultados

- 100 reqs, 1 VU: avg=5.08ms min=3.5ms med=4.55ms max=15.62ms p(90)=6.56ms p(95)=7.52ms; throughput = 189.08565 req/s
- 100 reqs, 5 VUs: avg=10.02ms min=4.8ms med=9.11ms max=32.55ms p(90)=12.12ms p(95)=14ms; throughput = 481.528692 req/s

O worker em ambos os casos demorou em torno de 40 segundos para terminar de processar as 100 mensagens da outbox (batch de 20 mensagens a cada 5 segundos).

# Confiabilidade

## Procedimento 1: Interrupção antes da emissão de evento na Saga original

Vamos interromper o Billing Service antes da emissão do evento `billing.payment_confirmed` e observar o comportamento do sistema. Existem várias formas de fazer isso, eu decidi colocar um timeout de 20 segundos logo após a confirmação do pagamento, ou seja, antes da publicação do evento, e então interromper o serviço.

## Resultado

Inicialmente o estado do sistema é um pedido pendente e um pagamento confirmado, mas um envio não realizado, e isso é de se esperar, afinal o evento de confirmação do pagamento não foi publicado, e consequentemente os serviços de envio e estoque não foram notificados.

No entanto, após a reinicialização do serviço, como a mensagem do Warehouse Service nunca foi reconhecida devido ao fluxo do nosso código, o código recebe a mensagem novamente e reexecuta o fluxo para o mesmo pedido, isso demonstra que o código implementado é idempotente, ou seja, a execução repetida do mesmo código com os mesmos dados de entrada não causa efeitos colaterais adicionais. Dessa forma, o sistema é capaz de se recuperar de falhas e garantir a consistência dos dados mesmo em situações de interrupção inesperada.

## Procedimento 2: Interrupção antes da emissão do evento na Saga com Outbox

Vamos repetir o mesmo procedimento, mas agora com a implementação utilizando Transactional Outbox. O objetivo é observar se o comportamento do sistema é diferente em relação à Saga original.

## Resultado

Do ponto de vista funcional, o comportamento observado foi semelhante ao da implementação anterior. O sistema permaneceu capaz de recuperar o processamento após a reinicialização do serviço.

Entretanto, nesta versão, a publicação do evento está associada à persistência de um registro na tabela de outbox dentro da mesma transação local. Como consequência, elimina-se a possibilidade de confirmação dos dados de negócio sem que exista um registro persistente representando o evento que precisa ser publicado posteriormente.

Assim, mesmo que a publicação para o broker não ocorra imediatamente, a intenção de publicação permanece armazenada e pode ser retomada posteriormente pelo worker responsável pelo processamento da outbox.

# Conclusão Parcial

Embora a literatura apresente o Transactional Outbox como uma solução para o problema de perda de eventos entre persistência de dados e publicação de mensagens, os experimentos realizados indicaram que a implementação desenvolvida já possuía mecanismos que mitigavam parcialmente esse risco. O uso de ACK tardio no RabbitMQ, combinado com consumidores idempotentes, permitiu a recuperação automática de falhas ocorridas durante o processamento da mensagem. Dessa forma, os benefícios do padrão mostraram-se menos evidentes no cenário experimental adotado.

Entretanto, o padrão continua relevante em cenários que exigem auditoria de eventos, reenvio controlado de mensagens, integração com múltiplos brokers ou garantia explícita de persistência dos eventos produzidos, características que não foram foco desta implementação.

Os resultados obtidos mostram que a efetividade de um padrão arquitetural depende diretamente do contexto em que ele é aplicado. Neste estudo, a combinação de ACK tardio do RabbitMQ e consumidores idempotentes já fornecia mecanismos relevantes de recuperação de falhas, reduzindo o impacto prático da introdução do Transactional Outbox. Ainda assim, o padrão adiciona garantias arquiteturais importantes relacionadas à persistência da intenção de publicação, o que pode ser decisivo em sistemas que exigem maior rastreabilidade, auditoria ou integração com múltiplos componentes externos.
