# ADR-001 · Ingestão semanal do CAUC

Status: aceito · 2026-11-05

## Contexto
O Tesouro publica no CKAN dois CSVs do CAUC (municípios e estados/DF), em blocos (metadados, cabeçalho, dados,
legenda), latin-1, com itens que entram e saem ao longo do tempo (4.3 e 5.8 só em estados). Destino: banco
`transferepro`, schema `base_tesouro_nacional`, objetos `cauc_*`.

## Decisões

1. **Stack igual às outras cargas de `APIS/`** (padrão de `transferencias_fundo_a_fundo`): Python 3.11+, httpx,
   psycopg 3, python-dotenv, `holidays`; sem ORM nem pandas; Agendador de Tarefas do Windows.
2. **DDL na migration do Laravel** (`database/migrations/base_tesouro_nacional/2026_11_05_120000_create_cauc_objects.php`),
   que lê `sql/cauc_ddl.sql`, cópia idêntica de `src/cauc_ingest/sql/ddl.sql`. `tests/test_agenda_contrato.py`
   falha se as duas divergirem. A DDL é idempotente.
3. **Fato longo (ente × item)**, não largo: item novo ou removido não exige migração de schema; só uma linha em
   `cauc_dim_item` (aprovada por humano com `cauc-ingest aprovar-item`).
4. **Snapshot completo por versão + SCD2.** `cauc_fato_situacao_item` e `cauc_fato_ente` guardam cada versão
   publicada ("como estava na semana X"). `cauc_hist_situacao_item` e `cauc_dim_ente` (SCD2) são **reconstruídos a
   partir dos snapshots** a cada publicação. Assim, republicação e backfill fora de ordem têm o mesmo resultado
   que cargas incrementais, sem lógica especial. Teto conhecido: reconstrução completa por tipo de ente; trocar por
   reconstrução a partir da data afetada se passar de alguns minutos.
5. **Chaves como texto** (`codigo_ibge`, `codigo_siafi`): SIAFI tem zero à esquerda (`0643`).
6. **Três estados + desconhecido**: `REGULAR` (com validade), `PENDENTE` (`!`), `NAO_AVALIADO` (`Desabilitado`, não
   reprova), `DESCONHECIDO` (vai para qualidade). Ano de 2 dígitos sempre `20aa`.
7. **`apto_transferencia`** = nenhum item com `considera_aptidao = true` em `PENDENTE`. É uma regra inferida,
   configurável por item em `cauc_dim_item.considera_aptidao` (a exigibilidade legal varia por tipo de transferência).
8. **Entidade do sistema = CNPJ** (`transferepro.entities.identif_proponente`). O CAUC não traz CNPJ, e as entidades não
   trazem IBGE de 7 dígitos nem SIAFI. Cascata do match:
   - `CNPJ_VIA_TABELA`: CNPJ principal do ente via `cauc_ref_cnpj_ente`, carregada da API de entes do Siconfi
     (atualização mensal). Validado automaticamente.
   - `ORGAO_VIA_IBGE6` / `ORGAO_VIA_UF`: órgãos com natureza jurídica pública (de `tab_propostas`) **herdam** a
     situação do município (`cod_municipio_6dig`) ou do estado (UF). Validado automaticamente.
   - `NOME_UF` (0,90) e `NOME_UF_FUZZY` (0,70, difflib ≥ 0,92, mesma UF, candidato único): pendentes de validação
     humana (`cauc-ingest validar-match`).
   - OSC, empresas e consórcios não são entes federados: ficam sem match.
   A visão `cauc_vw_situacao_entidade_sistema` mostra só matches validados, com `e_ente_principal` e `monitorada`.
9. **Alertas só em dry-run.** Todos vão para `cauc_alerta` (deduplicados por `chave_dedup`) e para um arquivo
   `data/alertas/alertas_<run_id>.txt`. Canais Email/Webhook ficam para quando canais e responsáveis forem definidos.
   Alertas de negócio só para entidades **monitoradas** (`transferepro.monitored_entities`) com match validado.
   `VENCIMENTO_PROXIMO` fica desligado (`CAUC_ALERTA_VENCIMENTO_ATIVO=false`); use `cauc-ingest distribuicao-vencimento`
   para escolher os itens antes de ligar.
10. **Retenção do raw**: nada é apagado (mínimo de 5 anos atendido). Particionamento do fato: não aplicado (cerca de 8 milhões
    de linhas/ano); avaliar acima de 50 milhões.

## Consequências
- A visão `cauc_vw_situacao_entidade_sistema` depende de colunas de `transferepro.entities` e `monitored_entities`
  (`identif_proponente`, `nome`, `uf_proponente`, `ativo`, `deleted_at`). Uma migration do Laravel que remova ou mude o
  tipo dessas colunas precisa recriar a visão.
- Usuário de banco dedicado ao pipeline (recomendado): escrita só em `base_tesouro_nacional.cauc_*`, leitura em
  `transferepro.entities`, `transferepro.monitored_entities` e `transfere_pro_transferegov.tab_propostas`.
