# Runbook · Carga do CAUC

Pasta: `D:\ProjetosSandro\transfere_pro\APIS\carga_tesouro_nacional\Cauc`. Os comandos abaixo assumem PowerShell
nessa pasta, com `.venv\Scripts\cauc-ingest.exe` (abreviado como `cauc-ingest`).

## Instalação e operação
```powershell
python -m venv .venv; .venv\Scripts\pip install -e ".[dev]"
copy .env.example .env    # preencher TRANSFEREPRO_DB_USER / PASSWORD
# DDL (no projeto Laravel; aplica SÓ a migration do CAUC):
cd D:\projetos_herd\transferepro; php artisan migrate --path=database/migrations/base_tesouro_nacional/2026_11_05_120000_create_cauc_objects.php
.\scripts\agendar_tarefa.ps1          # tarefa do Windows (PowerShell como Administrador)
.\scripts\executar_carga.ps1 --forcar # execução manual imediata
.venv\Scripts\pytest                  # unitários; integração: pytest -m integracao (banco TRANSFEREPRO_TEST_DB_NAME)
```
Onde olhar: `logs\` (JSON por linha), `data\relatorios\dq_report_<tipo>_<data>.md`, `data\relatorios\relatorio_match.csv`,
`data\alertas\`, tabelas `cauc_execucao`, `cauc_dq_resultado`, `cauc_alerta`.

## 1. Arquivo não atualizado no prazo (`ARQUIVO_NAO_ATUALIZADO`)
1. Conferir no portal: `https://www.tesourotransparente.gov.br/ckan/api/3/action/resource_show?id=07af297a-5e59-494a-a88a-55ddfd2f4b01` (`last_modified`).
2. Se o Tesouro publicou mais tarde: `cauc-ingest run --forcar`.
3. Se o portal está fora: aguardar; a próxima semana segue normal. A visão continua mostrando a última versão publicada.

## 2. Versão rejeitada por item novo no CAUC (`CAB_ITENS_CONHECIDOS`)
```powershell
# a mensagem do alerta/relatório traz o código e a descrição da legenda
cauc-ingest aprovar-item 5.9 --descricao "Descrição da legenda" --municipio --estado
cauc-ingest reprocess --tipo MUNICIPIO --data 2026-11-06
```
Item removido (`CAB_ITENS_AUSENTES`): confirmar no portal e rodar `cauc-ingest remover-item <codigo>` + `reprocess`.
Ajuste de aptidão/criticidade: `update base_tesouro_nacional.cauc_dim_item set considera_aptidao = false where codigo_item = '...'`.

## 3. Versão rejeitada por mudança de encoding/layout (`MUDANCA_DE_LAYOUT`)
- `META_ENCODING` é só aviso: o parser já leu em UTF-8. Confirme acentos no relatório de qualidade.
- `CAB_COLUNAS_FIXAS`, `LIN_LARGURA`, `META_*`: abrir o raw (`caminho_raw` em `cauc_carga_arquivo`), comparar com
  `docs/prompts/01-contrato-de-dados.md`, ajustar `parse.py`/`config.py` com teste novo em `tests/`, e `reprocess`.

## 4. Republicação do Tesouro na mesma data (`REPUBLICACAO`)
Automático: a nova versão é publicada, a anterior vira `SUBSTITUIDO`, snapshot/SCD2/eventos daquela data e das
seguintes são recalculados. Só conferir o relatório de qualidade.

## 5. Alerta errado: silenciar e corrigir
```powershell
cauc-ingest silenciar --ate 2026-12-31 --cnpj 46395000000139 --item 4.2 --motivo "erro na fonte"
```
Match errado: `update base_tesouro_nacional.cauc_map_entidade set tipo_ente = ..., codigo_ibge = ..., validado_por = 'nome' where id_entidade_sistema = '<cnpj>'`
(validação manual nunca é sobrescrita). Validar sugestões: `cauc-ingest validar-match <cnpj> --por <nome>`.

## 6. Reconstruir histórico a partir do raw
```powershell
cauc-ingest backfill --pasta data\raw              # cargas já registradas são ignoradas (mesmo sha256)
cauc-ingest reprocess --tipo ESTADO --data 2026-10-30   # refaz uma versão e reconstrói SCD2 e eventos
```
Reconstrução total: truncar `cauc_bronze_linha, cauc_fato_situacao_item, cauc_fato_ente, cauc_hist_situacao_item,
cauc_evento_mudanca, cauc_dim_ente, cauc_dq_metrica, cauc_carga_arquivo` (cascade) e rodar `backfill --pasta data\raw`.
O resultado é idêntico ao incremental (teste `test_backfill_fora_de_ordem_igual_ao_incremental`).

## Códigos de saída
0 ok/sem novidade/fora da janela · 1 erro de execução · 2 outra execução em andamento · 3 parcial (versão rejeitada ou erro em um tipo).
