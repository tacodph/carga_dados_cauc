# 06 · Histórico e versionamento semanal

## Papel
Engenheiro de dados. Contexto: `01-contrato-de-dados.md`, tabelas do prompt 02.

## Objetivo
Implementar `history.py`: manter a série semanal auditável e calcular o que mudou entre a versão nova e a anterior.

## Conceitos
- **Versão** = um arquivo aceito, identificado por `(tipo_ente, data_referencia, sha256)`.
- **Versão publicada** = a mais recente aceita para aquela `data_referencia`. Só ela alimenta gold e alertas.
- **Semana de referência** = semana ISO da `data_referencia` (`AAAA-Www`). Guardar também `semana_iso` para relatórios.

## Requisitos

### 1. Snapshot
`fato_situacao_item` já guarda o snapshot completo de cada `data_referencia`. Nunca apagar snapshots publicados. Particione por `data_referencia` (mensal ou anual) se o banco suportar.

### 2. SCD2 do status (`hist_situacao_item`)
Para cada `(tipo_ente, codigo_ibge, codigo_item)` da nova versão:
- Se `status` e `data_validade` iguais ao registro `atual` → nada muda.
- Se diferentes → fecha o atual (`valido_ate = data_referencia_nova - 1`) e abre novo (`valido_de = data_referencia_nova`).
- **Decisão de modelagem:** mudança apenas de `data_validade` em item `REGULAR` é uma **renovação**. Registre no SCD2 (é útil para auditoria), mas o evento é `RENOVADA`, não alerta.
- Permite responder: "há quantos dias o município X está com 4.2 pendente?" = `data_referencia_atual - valido_de` do registro atual.

### 3. Eventos de mudança (`evento_mudanca`)
Comparar a versão nova com a **última versão publicada anterior** do mesmo tipo de ente (não com "7 dias atrás": pode ter havido semana sem arquivo).

| De → Para | `tipo_evento` |
|---|---|
| REGULAR/NAO_AVALIADO → PENDENTE | `NOVA_PENDENCIA` |
| PENDENTE → REGULAR | `PENDENCIA_RESOLVIDA` |
| REGULAR → REGULAR com validade diferente | `RENOVADA` |
| qualquer → NAO_AVALIADO ou inverso | `MUDANCA_AVALIACAO` |
| item sem registro anterior | `ITEM_NOVO` |
| ente presente antes e ausente agora | `ENTE_AUSENTE` (não apagar histórico) |
| ente ausente antes e presente agora | `ENTE_NOVO` (ex.: município recém-instalado) |

### 4. Republicação na mesma data
Se chegar sha256 diferente para a mesma `data_referencia`: recalcular snapshot, SCD2 e eventos daquela data **e da seguinte, se existir**, em uma transação. A versão antiga vai para `SUBSTITUIDO`.

### 5. Backfill
`cli.py backfill --pasta <dir>` carrega arquivos antigos em ordem crescente de `data_referencia`. Se um arquivo mais antigo chegar depois de mais novos, reconstruir SCD2 e eventos a partir dele (rotina `rebuild_history(desde=data)`). O resultado do backfill deve ser idêntico ao de cargas incrementais na mesma ordem: teste isso.

### 6. Consultas que a solução deve responder (crie como visões ou funções)
- Situação de qualquer ente em qualquer semana passada.
- Linha do tempo de um ente: intervalos por item.
- Mudanças da semana (para o relatório e alertas).
- Tempo médio para regularizar por item e por UF (análise de sobrevivência simples: intervalos PENDENTE fechados e abertos/censurados).
- Série semanal de % de entes com pendência, por item e UF.

## Entregáveis
- `history.py`, visões `vw_mudancas_semana`, `vw_linha_do_tempo_ente`, `vw_serie_pendencias`.
- Testes com três versões sintéticas derivadas do arquivo real (semana 1 = real; semana 2 = 10 pendências resolvidas, 5 novas, 1 ente ausente; semana 3 = republicação da semana 2).

## Critérios de aceite
- Reprocessar qualquer versão não altera o resultado (idempotência verificada por hash do conteúdo das tabelas).
- Contagem de eventos nos testes bate exatamente com as alterações injetadas.
- A primeira carga não gera eventos `NOVA_PENDENCIA` (não há base de comparação): gera apenas a linha de base e um resumo.
