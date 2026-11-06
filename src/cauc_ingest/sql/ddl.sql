-- CAUC · banco transferepro · schema base_tesouro_nacional · só objetos com prefixo cauc_
-- Idempotente. Fonte única: a migration do Laravel (database/migrations/base_tesouro_nacional) carrega uma cópia
-- idêntica deste arquivo; tests/test_contrato.py garante que as duas não divergem.

create schema if not exists base_tesouro_nacional;

create table if not exists base_tesouro_nacional.cauc_carga_arquivo (
  id_carga           bigserial primary key,
  tipo_ente          text not null check (tipo_ente in ('MUNICIPIO','ESTADO')),
  data_referencia    date not null,
  semana_iso         text not null,
  sha256             char(64) not null,
  tamanho_bytes      bigint not null,
  url_origem         text not null,
  caminho_raw        text not null,
  ckan_last_modified timestamptz,
  baixado_em         timestamptz not null,
  encoding_detectado text,
  qtd_entes          int,
  colunas_itens      text[] not null default '{}',
  status             text not null check (status in ('RECEBIDO','VALIDADO','PUBLICADO','REJEITADO','SUBSTITUIDO')),
  com_ressalva       boolean not null default false,
  motivo_rejeicao    text,
  publicado_em       timestamptz,
  run_id             uuid not null,
  unique (tipo_ente, data_referencia, sha256)
);

create table if not exists base_tesouro_nacional.cauc_bronze_linha (
  id_carga  bigint not null references base_tesouro_nacional.cauc_carga_arquivo on delete cascade,
  num_linha int not null,
  dados     jsonb not null,
  primary key (id_carga, num_linha)
);

create table if not exists base_tesouro_nacional.cauc_dim_item (
  codigo_item       text primary key,
  grupo             text not null,
  subgrupo          text,
  descricao         text not null,
  aplica_municipio  boolean not null,
  aplica_estado     boolean not null,
  considera_aptidao boolean not null default true,  -- regra de apto_transferencia (configurável)
  critico_negocio   boolean not null default true,  -- alerta NOVA_PENDENCIA só para itens críticos
  vigente_desde     date,
  vigente_ate       date
);

-- snapshot dos atributos cadastrais por versão publicada (base do SCD2 de dim_ente e de "como estava na semana X")
create table if not exists base_tesouro_nacional.cauc_fato_ente (
  data_referencia  date not null,
  tipo_ente        text not null,
  codigo_ibge      text not null,
  codigo_siafi     text not null,
  uf               char(2) not null,
  nome             text not null,
  nome_normalizado text not null,
  regiao           text not null,
  populacao        bigint,
  fonte_populacao  text,
  id_carga         bigint not null references base_tesouro_nacional.cauc_carga_arquivo,
  primary key (data_referencia, tipo_ente, codigo_ibge)
);

create table if not exists base_tesouro_nacional.cauc_dim_ente (
  sk_ente          bigserial primary key,
  tipo_ente        text not null,
  codigo_ibge      text not null,
  codigo_siafi     text not null,
  uf               char(2) not null,
  nome             text not null,
  nome_normalizado text not null,
  regiao           text not null,
  populacao        bigint,
  fonte_populacao  text,
  valido_de        date not null,
  valido_ate       date,
  atual            boolean not null
);
create unique index if not exists cauc_dim_ente_atual_uk
  on base_tesouro_nacional.cauc_dim_ente (tipo_ente, codigo_ibge) where atual;

create table if not exists base_tesouro_nacional.cauc_fato_situacao_item (
  data_referencia  date not null,
  tipo_ente        text not null,
  codigo_ibge      text not null,
  codigo_item      text not null references base_tesouro_nacional.cauc_dim_item,
  valor_bruto      text not null,
  status           text not null check (status in ('REGULAR','PENDENTE','NAO_AVALIADO','DESCONHECIDO')),
  data_validade    date,
  dias_para_vencer int,
  id_carga         bigint not null references base_tesouro_nacional.cauc_carga_arquivo,
  primary key (data_referencia, tipo_ente, codigo_ibge, codigo_item)
);
create index if not exists cauc_fato_situacao_item_ente_ix
  on base_tesouro_nacional.cauc_fato_situacao_item (tipo_ente, codigo_ibge, codigo_item, data_referencia);

create table if not exists base_tesouro_nacional.cauc_hist_situacao_item (
  tipo_ente     text not null,
  codigo_ibge   text not null,
  codigo_item   text not null,
  status        text not null,
  data_validade date,
  valido_de     date not null,
  valido_ate    date,
  atual         boolean not null,
  primary key (tipo_ente, codigo_ibge, codigo_item, valido_de)
);

create table if not exists base_tesouro_nacional.cauc_evento_mudanca (
  data_referencia          date not null,
  data_referencia_anterior date,
  tipo_ente                text not null,
  codigo_ibge              text not null,
  codigo_item              text not null,  -- '*' em ENTE_NOVO / ENTE_AUSENTE
  status_anterior          text,
  status_atual             text,
  validade_anterior        date,
  validade_atual           date,
  tipo_evento              text not null,
  primary key (data_referencia, tipo_ente, codigo_ibge, codigo_item, tipo_evento)
);

create table if not exists base_tesouro_nacional.cauc_ref_cnpj_ente (
  cnpj          char(14) primary key,
  tipo_ente     text not null,
  codigo_ibge   text not null,
  nome          text,
  fonte         text not null,
  atualizado_em timestamptz not null default now()
);

create table if not exists base_tesouro_nacional.cauc_map_entidade (
  id_entidade_sistema text primary key,  -- CNPJ (transferepro.entities.identif_proponente)
  tipo_ente     text not null,
  codigo_ibge   text not null,
  metodo_match  text not null,
  confianca     numeric(3,2) not null,
  validado_por  text,
  validado_em   timestamptz,
  atualizado_em timestamptz not null default now()
);

create table if not exists base_tesouro_nacional.cauc_dq_resultado (
  run_id          uuid,
  id_carga        bigint,
  regra           text not null,
  severidade      text not null,
  passou          boolean not null,
  valor_observado numeric,
  limite          text,
  detalhe         jsonb,
  criado_em       timestamptz not null default now()
);
create index if not exists cauc_dq_resultado_carga_ix on base_tesouro_nacional.cauc_dq_resultado (id_carga);

create table if not exists base_tesouro_nacional.cauc_dq_metrica (
  data_referencia date not null,
  tipo_ente       text not null,
  metrica         text not null,
  dimensao        text not null default '',
  valor           numeric not null,
  run_id          uuid,
  primary key (data_referencia, tipo_ente, metrica, dimensao)
);

create table if not exists base_tesouro_nacional.cauc_alerta (
  id                  bigserial primary key,
  run_id              uuid,
  chave_dedup         text not null unique,
  tipo                text not null,
  severidade          text not null,
  id_entidade_sistema text,
  data_referencia     date,
  payload             jsonb,
  criado_em           timestamptz not null default now(),
  enviado_em          timestamptz,
  canal               text
);

create table if not exists base_tesouro_nacional.cauc_alerta_silencio (
  id                  bigserial primary key,
  id_entidade_sistema text,  -- null = todas
  codigo_item         text,  -- null = todos
  tipo                text,  -- null = todos
  ate                 date not null,
  motivo              text,
  criado_em           timestamptz not null default now()
);

create table if not exists base_tesouro_nacional.cauc_execucao (
  run_id          uuid not null,
  tipo_ente       text not null,  -- MUNICIPIO | ESTADO | GERAL
  inicio          timestamptz not null,
  fim             timestamptz,
  status          text not null,
  data_referencia date,
  etapas          jsonb not null default '{}',
  erro            text,
  primary key (run_id, tipo_ente)
);

-- ===================== visões =====================

create or replace view base_tesouro_nacional.cauc_vw_resumo_ente as
select f.data_referencia, f.tipo_ente, f.codigo_ibge, e.codigo_siafi, e.uf, e.nome,
       count(*) filter (where f.status = 'PENDENTE')     as qtd_pendentes,
       count(*) filter (where f.status = 'REGULAR')      as qtd_regulares,
       count(*) filter (where f.status = 'NAO_AVALIADO') as qtd_nao_avaliados,
       count(*) filter (where f.status = 'DESCONHECIDO') as qtd_desconhecidos,
       coalesce(array_agg(f.codigo_item order by string_to_array(f.codigo_item, '.')::int[])
                filter (where f.status = 'PENDENTE'), '{}') as itens_pendentes,
       not bool_or(f.status = 'PENDENTE' and i.considera_aptidao) as apto_transferencia,
       min(f.data_validade) filter (where f.status = 'REGULAR') as menor_validade,
       count(*) filter (where f.status = 'PENDENTE' and i.grupo = 'I')   as pendentes_grupo_i,
       count(*) filter (where f.status = 'PENDENTE' and i.grupo = 'II')  as pendentes_grupo_ii,
       count(*) filter (where f.status = 'PENDENTE' and i.grupo = 'III') as pendentes_grupo_iii,
       count(*) filter (where f.status = 'PENDENTE' and i.grupo = 'IV')  as pendentes_grupo_iv,
       count(*) filter (where f.status = 'PENDENTE' and i.grupo = 'V')   as pendentes_grupo_v
from base_tesouro_nacional.cauc_fato_situacao_item f
join base_tesouro_nacional.cauc_dim_item i using (codigo_item)
join base_tesouro_nacional.cauc_fato_ente e using (data_referencia, tipo_ente, codigo_ibge)
group by f.data_referencia, f.tipo_ente, f.codigo_ibge, e.codigo_siafi, e.uf, e.nome;

create or replace view base_tesouro_nacional.cauc_vw_situacao_atual as
select r.*,
       to_char(r.data_referencia, 'IYYY-"W"IW') as semana_iso,
       r.data_referencia - p.pendente_desde as dias_pendente_mais_antigo
from base_tesouro_nacional.cauc_vw_resumo_ente r
join (select tipo_ente, max(data_referencia) as data_referencia
      from base_tesouro_nacional.cauc_carga_arquivo where status = 'PUBLICADO' group by tipo_ente) u
  using (tipo_ente, data_referencia)
left join (select tipo_ente, codigo_ibge, min(valido_de) as pendente_desde
           from base_tesouro_nacional.cauc_hist_situacao_item
           where atual and status = 'PENDENTE' group by tipo_ente, codigo_ibge) p
  using (tipo_ente, codigo_ibge);

-- Entidade do sistema = CNPJ de transferepro.entities. A situação exibida é SEMPRE a do ente principal
-- (prefeitura / governo do estado); e_ente_principal indica se o CNPJ é o do próprio ente.
create or replace view base_tesouro_nacional.cauc_vw_situacao_entidade_sistema as
select m.id_entidade_sistema, en.nome as nome_entidade, en.uf_proponente,
       m.metodo_match, m.confianca,
       exists (select 1 from base_tesouro_nacional.cauc_ref_cnpj_ente r where r.cnpj = m.id_entidade_sistema) as e_ente_principal,
       exists (select 1 from transferepro.monitored_entities me
               where me.identif_proponente = m.id_entidade_sistema and me.ativo and me.deleted_at is null) as monitorada,
       s.tipo_ente, s.codigo_ibge, s.nome as nome_ente_cauc, s.uf, s.data_referencia, s.semana_iso,
       s.apto_transferencia, s.qtd_pendentes, s.itens_pendentes, s.menor_validade, s.dias_pendente_mais_antigo
from base_tesouro_nacional.cauc_map_entidade m
join transferepro.entities en on en.identif_proponente = m.id_entidade_sistema
join base_tesouro_nacional.cauc_vw_situacao_atual s using (tipo_ente, codigo_ibge)
where m.validado_por is not null;

create or replace view base_tesouro_nacional.cauc_vw_mudancas_semana as
select ev.*, to_char(ev.data_referencia, 'IYYY-"W"IW') as semana_iso, e.nome, e.uf, i.descricao as descricao_item
from base_tesouro_nacional.cauc_evento_mudanca ev
join (select tipo_ente, max(data_referencia) as data_referencia
      from base_tesouro_nacional.cauc_carga_arquivo where status = 'PUBLICADO' group by tipo_ente) u
  using (tipo_ente, data_referencia)
left join base_tesouro_nacional.cauc_dim_ente e on e.tipo_ente = ev.tipo_ente and e.codigo_ibge = ev.codigo_ibge and e.atual
left join base_tesouro_nacional.cauc_dim_item i on i.codigo_item = ev.codigo_item;

create or replace view base_tesouro_nacional.cauc_vw_linha_do_tempo_ente as
select h.tipo_ente, h.codigo_ibge, e.nome, e.uf, h.codigo_item, i.descricao as descricao_item,
       h.status, h.data_validade, h.valido_de, h.valido_ate, h.atual,
       coalesce(h.valido_ate, u.data_referencia) - h.valido_de + 1 as duracao_dias
from base_tesouro_nacional.cauc_hist_situacao_item h
join (select tipo_ente, max(data_referencia) as data_referencia
      from base_tesouro_nacional.cauc_fato_ente group by tipo_ente) u using (tipo_ente)
left join base_tesouro_nacional.cauc_dim_ente e on e.tipo_ente = h.tipo_ente and e.codigo_ibge = h.codigo_ibge and e.atual
left join base_tesouro_nacional.cauc_dim_item i on i.codigo_item = h.codigo_item;

create or replace view base_tesouro_nacional.cauc_vw_serie_pendencias as
select f.data_referencia, to_char(f.data_referencia, 'IYYY-"W"IW') as semana_iso, f.tipo_ente, e.uf, f.codigo_item,
       count(*) as qtd_entes,
       count(*) filter (where f.status = 'PENDENTE') as qtd_pendentes,
       round(100.0 * count(*) filter (where f.status = 'PENDENTE') / count(*), 2) as pct_pendentes
from base_tesouro_nacional.cauc_fato_situacao_item f
join base_tesouro_nacional.cauc_fato_ente e using (data_referencia, tipo_ente, codigo_ibge)
group by f.data_referencia, f.tipo_ente, e.uf, f.codigo_item;

-- Análise de sobrevivência simples: intervalos PENDENTE fechados (regularizados) e abertos (censurados).
create or replace view base_tesouro_nacional.cauc_vw_tempo_regularizacao as
select h.tipo_ente, h.codigo_item, e.uf,
       count(*) filter (where not h.atual)                                   as intervalos_fechados,
       count(*) filter (where h.atual)                                       as intervalos_abertos,
       round(avg(h.valido_ate - h.valido_de + 1) filter (where not h.atual), 1) as media_dias_para_regularizar,
       percentile_cont(0.5) within group (order by h.valido_ate - h.valido_de + 1)
         filter (where not h.atual)                                          as mediana_dias_para_regularizar
from base_tesouro_nacional.cauc_hist_situacao_item h
left join base_tesouro_nacional.cauc_dim_ente e on e.tipo_ente = h.tipo_ente and e.codigo_ibge = h.codigo_ibge and e.atual
where h.status = 'PENDENTE'
group by h.tipo_ente, h.codigo_item, e.uf;
