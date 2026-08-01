# Experimentos possíveis de realizar em cada arquitetura

| Experimento           | Base | 2PC | TCC | Saga | Outbox |  ES | CQRS |
| --------------------- | :--: | :-: | :-: | :--: | :----: | :-: | :--: |
| Atomicidade           |   ✓  |  ✓  |  ✓  |   ✓  |    —   |  —  |   —  |
| Latência              |   ✓  |  ✓  |  ✓  |   ✓  |    ✓   |  ✓  |   ✓  |
| Throughput            |   ✓  |  ✓  |  ✓  |   ✓  |    ✓   |  ✓  |   ✓  |
| Consistência Eventual |   —  |  —  |  —  |   —  |    —   |  ✓  |   ✓  |
| Recuperação           |   —  |  —  |  —  |   ✓  |    ✓   |  ✓  |   ✓  |
| Auditoria             |   —  |  —  |  —  |   —  |    —   |  ✓  |   ✓  |
