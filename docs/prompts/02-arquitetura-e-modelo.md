# 02 · Arquitetura e modelo de dados

## Papel
Você é um engenheiro de dados sênior. Vai desenhar e criar o esqueleto do pipeline de ingestão do CAUC e o modelo físico no banco. Use o `01-contrato-de-dados.md` como fonte da verdade e os parâmetros do ambiente informados no início.

## Objetivo
Criar a estrutura do projeto e as tabelas, sem lógica de negócio ainda, de forma que as etapas 03 a 10 só preencham módulos já definidos.

## Destino obrigatório: banco `transferepro`, schema `base_tesouro_nacional`
- Todas as tabelas e visões do CAUC ficam no banco **`transferepro`**, schema **`base_tesouro_nacional`**. Não crie outro schema.
- O schema pode já existir e ter tabelas de outras cargas do Tesouro. Por isso todo objeto desta carga usa o prefixo **`cauc_`** (tabelas `cauc_*`, visões `cauc_vw_*`). Nas etapas seguintes, nomes curtos como `vw_situacao_atual` ou `map_entidade` significam `base_tesouro_nacional.cauc_vw_situacao_atual` e `base_tesouro_nacional.cauc_map_entidade`.
- **Antes de criar qualquer coisa**, conecte no `transferepro` e:
  1. Confirme o SGBD e a versão. O DDL abaixo é PostgreSQL. Se for outro SGBD, traduza os tipos (`jsonb`, `text[]`, `bigserial`, índice parcial).
  2. Liste os objetos que já existem em `base_tesouro_nacional`. Nunca faça `drop` nem `alter` em objetos que não tenham o prefixo `cauc_`. Se já houver tabelas `cauc_*`, pare e mostre ao usuário antes de alterar.
  3. Localize no mesmo banco a tabela de entidades do sistema (parâmetro `tabela_entidades_sistema`). O cruzamento do prompt 07 é uma junção entre schemas dentro do `transferepro`.
  4. Se o projeto transferepro já tiver um mecanismo de migração (por exemplo, migrations do Laravel), crie as migrações do CAUC nele em vez de introduzir outra ferramenta.
- Credenciais do `transferepro` só por variável de ambiente (`TRANSFEREPRO_DB_*`), nunca no código.

## Arquitetura em camadas (medallion)

```
CKAN Tesouro ──> [raw]    arquivo original imutável + manifesto (hash, tamanho, URL, horário)
             ──> [bronze] linhas do CSV como texto, 1:1 com o arquivo, + metadados do cabeçalho
             ──> [silver] fato longo ente × item tipado, dimensões, histórico SCD2
             ──> [gold]   visão por entidade do sistema, mudanças da semana, indicadores, alertas
```

## Estrutura de projeto sugerida (Python; adapte à stack informada)

```
cauc_ingest/
  config.py            # parâmetros, URLs, ids de recurso, faixas esperadas
  collect.py           # etapa 03
  parse.py             # etapa 04
  validate.py          # etapa 04 e 08
  normalize.py         # etapa 05
  history.py           # etapa 06
  match.py             # etapa 07
  quality.py           # etapa 08
  alerts.py            # etapa 09
  pipeline.py          # orquestra uma execução ponta a ponta (etapa 10)
  cli.py               # run | backfill --file | reprocess --versao | dry-run
sql/migrations/        # DDL versionado (Alembic, Flyway ou equivalente)
tests/fixtures/        # os dois CSVs reais de docs/ + variantes quebradas
```

## DDL mínimo (PostgreSQL; traduza para o banco informado)

```sql
create schema if not exists base_tesouro_nacional;   -- banco transferepro

-- controle de versões de arquivo (uma linha por arquivo baixado e aceito)
create table base_tesouro_nacional.cauc_carga_arquivo (
  id_carga          bigserial primary key,
  tipo_ente         text not null check (tipo_ente in ('MUNICIPIO','ESTADO')),
  data_referencia   date not null,              -- "Data da Pesquisa"
  sha256            char(64) not null,
  tamanho_bytes     bigint not null,
  url_origem        text not null,
  caminho_raw       text not null,
  ckan_last_modified timestamptz,
  baixado_em        timestamptz not null,
  qtd_entes         int,
  colunas_itens     text[] not null,
  status            text not null check (status in ('RECEBIDO','VALIDADO','PUBLICADO','REJEITADO','SUBSTITUIDO')),
  motivo_rejeicao   text,
  run_id            uuid not null,
  unique (tipo_ente, data_referencia, sha256)
);

-- bronze: texto cru
create table base_tesouro_nacional.cauc_bronze_linha (
  id_carga   bigint references base_tesouro_nacional.cauc_carga_arquivo,
  num_linha  int,
  dados      jsonb not null,            -- {"UF":"AC","Código IBGE":"1200013","1.1":"!",...}
  primary key (id_carga, num_linha)
);

-- dimensões
create table base_tesouro_nacional.cauc_dim_item (
  codigo_item   text primary key,       -- '3.2.4'
  grupo         text not null,          -- 'III'
  subgrupo      text,                   -- '3.2'
  descricao     text not null,
  aplica_municipio boolean, aplica_estado boolean,
  vigente_desde date, vigente_ate date
);

create table base_tesouro_nacional.cauc_dim_ente (               -- SCD2 dos atributos cadastrais
  sk_ente       bigserial primary key,
  tipo_ente     text not null,
  codigo_ibge   text not null,
  codigo_siafi  text not null,
  uf            char(2) not null,
  nome          text not null,
  regiao        text not null,
  populacao     bigint,
  valido_de     date not null,
  valido_ate    date,                    -- null = atual
  atual         boolean not null
);
create unique index on base_tesouro_nacional.cauc_dim_ente (tipo_ente, codigo_ibge) where atual;

-- silver: fato snapshot semanal (uma linha por ente × item × data_referencia)
create table base_tesouro_nacional.cauc_fato_situacao_item (
  data_referencia date not null,
  tipo_ente       text not null,
  codigo_ibge     text not null,
  codigo_item     text not null references base_tesouro_nacional.cauc_dim_item,
  valor_bruto     text not null,
  status          text not null check (status in ('REGULAR','PENDENTE','NAO_AVALIADO','DESCONHECIDO')),
  data_validade   date,
  dias_para_vencer int,
  id_carga        bigint not null references base_tesouro_nacional.cauc_carga_arquivo,
  primary key (data_referencia, tipo_ente, codigo_ibge, codigo_item)
);

-- silver: histórico compactado por intervalo (SCD2 do status do item)
create table base_tesouro_nacional.cauc_hist_situacao_item (
  tipo_ente text, codigo_ibge text, codigo_item text,
  status text, data_validade date,
  valido_de date not null, valido_ate date, atual boolean not null,
  primary key (tipo_ente, codigo_ibge, codigo_item, valido_de)
);

-- gold: mudanças entre versões consecutivas
create table base_tesouro_nacional.cauc_evento_mudanca (
  data_referencia date, data_referencia_anterior date,
  tipo_ente text, codigo_ibge text, codigo_item text,
  status_anterior text, status_atual text,
  validade_anterior date, validade_atual date,
  tipo_evento text,   -- NOVA_PENDENCIA | PENDENCIA_RESOLVIDA | RENOVADA | ITEM_NOVO | ITEM_REMOVIDO | ENTE_NOVO | ENTE_AUSENTE
  primary key (data_referencia, tipo_ente, codigo_ibge, codigo_item, tipo_evento)
);

-- correspondência com o sistema do usuário (etapa 07)
create table base_tesouro_nacional.cauc_map_entidade (
  id_entidade_sistema text not null,
  tipo_ente text not null, codigo_ibge text not null,
  metodo_match text not null,   -- IBGE | SIAFI | CNPJ_VIA_TABELA | NOME_UF_MANUAL
  confianca numeric(3,2) not null,
  validado_por text, validado_em timestamptz,
  primary key (id_entidade_sistema)
);

-- qualidade e alertas (etapas 08 e 09)
create table base_tesouro_nacional.cauc_dq_resultado (run_id uuid, id_carga bigint, regra text, severidade text,
  passou boolean, valor_observado numeric, limite text, detalhe jsonb, criado_em timestamptz default now());
create table base_tesouro_nacional.cauc_alerta (id bigserial primary key, run_id uuid, chave_dedup text unique,
  tipo text, severidade text, id_entidade_sistema text, payload jsonb,
  criado_em timestamptz default now(), enviado_em timestamptz, canal text);
```

Visões a criar já agora (podem ficar vazias):
- `base_tesouro_nacional.cauc_vw_situacao_atual`: último `data_referencia` publicado por `tipo_ente`, uma linha por ente com contagem de pendências e lista de itens pendentes.
- `base_tesouro_nacional.cauc_vw_situacao_entidade_sistema`: `vw_situacao_atual` + `map_entidade` + atributos da tabela de entidades do sistema.

## Decisões que você deve registrar em `docs/ADR-001-cauc.md`
1. Por que fato **longo** (ente × item) e não largo: itens entram e saem (4.3 e 5.8 só em estados) sem migração de schema.
2. Por que guardar snapshot semanal completo **e** SCD2: snapshot facilita auditoria ("como estava na semana X"); SCD2 facilita "há quanto tempo está pendente".
3. Chaves como **texto** (zeros à esquerda no SIAFI).

## Critérios de aceite
- Migrações aplicam do zero e são reexecutáveis.
- `cli.py --help` lista os comandos.
- Nenhuma lógica de negócio implementada fora dos módulos indicados.
