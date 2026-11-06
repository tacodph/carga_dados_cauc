# 10 · Orquestração e operação

## Papel
Engenheiro de dados / plataforma. Contexto: todos os prompts anteriores e o parâmetro `orquestrador`.

## Objetivo
Implementar `pipeline.py` e o agendamento, para que a carga rode sozinha toda semana, seja segura de reexecutar e fácil de operar.

## Fluxo de uma execução
```
para tipo_ente em [ESTADO, MUNICIPIO] (independentes, podem rodar em paralelo):
  03 coletar  -> sem novidade? encerra OK
  04 parse + validação estrutural  -> falhou? REJEITADO, alerta, encerra
  05 normalizar (staging)
  08 qualidade -> BLOQUEANTE? REJEITADO, alerta, encerra
  06 histórico + eventos   ┐ mesma transação de publicação:
  marcar PUBLICADO          ┘ ou tudo, ou nada
07 recalcular match / visão por entidade
09 gerar e enviar alertas
gerar relatório da execução
```

Use `run_id` (UUID) em todos os registros e logs da execução.

## Agendamento: "primeiro dia útil da semana"
- Calendário de dias úteis: fins de semana + feriados nacionais (use a biblioteca `holidays` com `BR` ou uma tabela `base_tesouro_nacional.cauc_calendario` editável; feriados como Carnaval e Corpus Christi são pontos facultativos federais e devem estar configuráveis).
- **Janela de tentativas**: disparar no primeiro dia útil da semana às 08:00, 12:00 e 18:00 (`America/Sao_Paulo`), e no dia útil seguinte às 08:00. Cada disparo encerra cedo se a versão da semana já foi publicada.
- **Como saber que é a versão da semana**: a `data_referencia` do arquivo é **posterior** à da última versão publicada. Não exigir que seja igual à data da execução (a extração analisada é de uma sexta, 30/10/2026).
- Se a janela terminar sem versão nova → alerta `ARQUIVO_NAO_ATUALIZADO` (uma vez).
- Implementação conforme o orquestrador: cron com verificação de dia útil no início do script; DAG do Airflow com `ShortCircuitOperator`; workflow do n8n com nó de Cron + nó de código para o calendário; GitHub Actions com `schedule`.
- **[Opcional]** Como o portal divulga em dias úteis, oferecer um modo "diário" desligado por padrão, já suportado pelo mesmo código.

## Robustez
- **Idempotência**: qualquer etapa pode ser repetida. Chaves naturais + upsert + `chave_dedup` garantem isso.
- **Concorrência**: lock por `tipo_ente` (advisory lock no Postgres ou arquivo de lock) para impedir duas execuções simultâneas.
- **Atomicidade**: a publicação (silver + histórico + status) é uma transação. Em falha, a versão anterior continua sendo a vigente.
- **Reprocesso**: `cli.py reprocess --tipo MUNICIPIO --data 2026-10-30` a partir do raw, sem baixar de novo.
- **Backfill**: `cli.py backfill --pasta <dir>` (prompt 06).

## Observabilidade
- Logs estruturados (JSON) com `run_id`, etapa, `tipo_ente`, `data_referencia`, duração, linhas.
- Tabela `base_tesouro_nacional.cauc_execucao(run_id, inicio, fim, status, tipo_ente, data_referencia, etapas jsonb, erro)`.
- Métricas para painel: duração por etapa, linhas por camada, % pendência por item na semana, cobertura de match, nº de alertas.
- Health-check: "última versão publicada há mais de 9 dias" → alerta.

## Segurança e configuração
- Segredos (banco, SMTP, webhooks) só por variável de ambiente ou cofre. `.env.example` sem valores.
- Usuário de banco do pipeline com permissão apenas no schema `base_tesouro_nacional` (apenas tabelas `cauc_*`) e leitura na tabela de entidades do sistema.
- Dados são públicos, mas a ligação com as entidades do sistema e os alertas podem não ser: aplicar as mesmas permissões do sistema de origem às visões gold.

## Runbook (gerar `docs/RUNBOOK-cauc.md`)
Cobrir, com comandos prontos:
1. Arquivo não atualizado no prazo.
2. Versão rejeitada por item novo no CAUC (aprovar item, reprocessar).
3. Versão rejeitada por mudança de encoding/layout.
4. Republicação do Tesouro na mesma data.
5. Alerta enviado errado: como silenciar e corrigir.
6. Reconstruir histórico a partir do raw.

## Critérios de aceite
- Simular uma semana completa com relógio controlado (`freezegun` ou injeção de data), incluindo segunda-feira feriado: a execução ocorre na terça.
- Duas execuções concorrentes: uma espera ou sai, nunca duplica.
- Matar o processo no meio da publicação deixa o banco consistente.
