"""Etapa 06: SCD2 (dim_ente, hist_situacao_item) e eventos de mudança entre versões publicadas consecutivas."""

from dataclasses import dataclass
from datetime import date

from .db import q

Snapshot = dict[tuple[str, str], tuple[str, date | None]]  # (ibge, item) -> (status, data_validade)


@dataclass(frozen=True)
class Evento:
    codigo_ibge: str
    codigo_item: str  # '*' para eventos de ente
    tipo_evento: str
    status_anterior: str | None = None
    status_atual: str | None = None
    validade_anterior: date | None = None
    validade_atual: date | None = None


def calcular_eventos(anterior: Snapshot, atual: Snapshot) -> list[Evento]:
    """Compara duas versões publicadas consecutivas. Primeira carga (anterior vazio) não gera eventos."""
    if not anterior:
        return []
    entes_ant = {i for i, _ in anterior}
    entes_atu = {i for i, _ in atual}
    eventos = [Evento(i, "*", "ENTE_NOVO") for i in sorted(entes_atu - entes_ant)]
    eventos += [Evento(i, "*", "ENTE_AUSENTE") for i in sorted(entes_ant - entes_atu)]
    for chave in sorted(anterior.keys() | atual.keys()):
        ibge, item = chave
        if ibge not in entes_ant or ibge not in entes_atu:
            continue
        if chave not in anterior:
            s1, v1 = atual[chave]
            eventos.append(Evento(ibge, item, "ITEM_NOVO", None, s1, None, v1))
            continue
        if chave not in atual:
            s0, v0 = anterior[chave]
            eventos.append(Evento(ibge, item, "ITEM_REMOVIDO", s0, None, v0, None))
            continue
        (s0, v0), (s1, v1) = anterior[chave], atual[chave]
        if s0 == s1:
            tipo = "RENOVADA" if s1 == "REGULAR" and v0 != v1 else None
        elif s1 == "PENDENTE":
            tipo = "NOVA_PENDENCIA"
        elif s0 == "PENDENTE" and s1 == "REGULAR":
            tipo = "PENDENCIA_RESOLVIDA"
        else:
            tipo = "MUDANCA_AVALIACAO"  # envolve NAO_AVALIADO ou DESCONHECIDO
        if tipo:
            eventos.append(Evento(ibge, item, tipo, s0, s1, v0, v1))
    return eventos


def datas_publicadas(conn, tipo: str) -> list[date]:
    return [r[0] for r in conn.execute(
        q("select distinct data_referencia from {s}.cauc_fato_ente where tipo_ente = %s order by 1"), (tipo,))]


def carregar_snapshot(conn, tipo: str, data_ref: date | None) -> Snapshot:
    if data_ref is None:
        return {}
    cur = conn.execute(q("""select codigo_ibge, codigo_item, status, data_validade from {s}.cauc_fato_situacao_item
                             where tipo_ente = %s and data_referencia = %s"""), (tipo, data_ref))
    return {(i, it): (st, v) for i, it, st, v in cur}


# Ilhas (gaps-and-islands): nova ilha quando o valor muda OU o ente/item faltou na versão publicada anterior.
# ponytail: reconstrói o SCD2 inteiro do tipo a cada publicação (~156 mil linhas/semana nos municípios);
# trocar por reconstrução a partir de `desde` se passar de alguns minutos.
SQL_HIST = """
delete from {s}.cauc_hist_situacao_item where tipo_ente = %(t)s;
insert into {s}.cauc_hist_situacao_item
  (tipo_ente, codigo_ibge, codigo_item, status, data_validade, valido_de, valido_ate, atual)
with pub as (
  select data_referencia, lag(data_referencia) over (order by data_referencia) as ant,
         lead(data_referencia) over (order by data_referencia) as prox
  from (select distinct data_referencia from {s}.cauc_fato_ente where tipo_ente = %(t)s) d
), marc as (
  select f.codigo_ibge, f.codigo_item, f.status, f.data_validade, f.data_referencia, p.prox,
         case when lag(f.data_referencia) over w is not distinct from p.ant
               and lag(f.status) over w = f.status
               and lag(f.data_validade) over w is not distinct from f.data_validade
              then 0 else 1 end as quebra
  from {s}.cauc_fato_situacao_item f join pub p using (data_referencia)
  where f.tipo_ente = %(t)s
  window w as (partition by f.codigo_ibge, f.codigo_item order by f.data_referencia)
), ilhas as (
  select *, sum(quebra) over (partition by codigo_ibge, codigo_item order by data_referencia) as ilha from marc
), fim as (
  select codigo_ibge, codigo_item, ilha, min(status) as status, min(data_validade) as data_validade,
         min(data_referencia) as valido_de, (array_agg(prox order by data_referencia desc))[1] as prox_ultima
  from ilhas group by codigo_ibge, codigo_item, ilha
)
select %(t)s, codigo_ibge, codigo_item, status, data_validade, valido_de, prox_ultima - 1, prox_ultima is null
from fim;
"""

# dim_ente: nova versão só quando atributo cadastral muda; ente que some não é fechado (vira evento ENTE_AUSENTE).
SQL_DIM_ENTE = """
delete from {s}.cauc_dim_ente where tipo_ente = %(t)s;
insert into {s}.cauc_dim_ente (tipo_ente, codigo_ibge, codigo_siafi, uf, nome, nome_normalizado, regiao,
                               populacao, fonte_populacao, valido_de, valido_ate, atual)
with marc as (
  select e.*, case when (lag(e.nome) over w, lag(e.codigo_siafi) over w, lag(e.uf) over w, lag(e.regiao) over w,
                         lag(e.populacao) over w)
                   is not distinct from (e.nome, e.codigo_siafi, e.uf, e.regiao, e.populacao) then 0 else 1 end as quebra
  from {s}.cauc_fato_ente e where e.tipo_ente = %(t)s
  window w as (partition by e.codigo_ibge order by e.data_referencia)
), ilhas as (
  select *, sum(quebra) over (partition by codigo_ibge order by data_referencia) as ilha from marc
), v as (
  select distinct on (codigo_ibge, ilha) codigo_ibge, ilha, codigo_siafi, uf, nome, nome_normalizado, regiao,
         populacao, fonte_populacao, data_referencia as valido_de
  from ilhas order by codigo_ibge, ilha, data_referencia
)
select %(t)s, codigo_ibge, codigo_siafi, uf, nome, nome_normalizado, regiao, populacao, fonte_populacao, valido_de,
       lead(valido_de) over (partition by codigo_ibge order by ilha) - 1,
       lead(valido_de) over (partition by codigo_ibge order by ilha) is null
from v;
"""


def reconstruir_scd2(conn, tipo: str) -> None:
    for comando in (SQL_HIST, SQL_DIM_ENTE):
        for parte in comando.split(";\n"):
            if parte.strip():
                conn.execute(q(parte), {"t": tipo})


def reconstruir_eventos(conn, tipo: str, desde: date) -> int:
    """Recalcula eventos de todas as versões publicadas >= desde (republicação e backfill fora de ordem)."""
    datas = datas_publicadas(conn, tipo)
    conn.execute(q("delete from {s}.cauc_evento_mudanca where tipo_ente = %s and data_referencia >= %s"), (tipo, desde))
    total = 0
    for i, d in enumerate(datas):
        if d < desde:
            continue
        anterior_data = datas[i - 1] if i else None
        eventos = calcular_eventos(carregar_snapshot(conn, tipo, anterior_data), carregar_snapshot(conn, tipo, d))
        with conn.cursor().copy(q("""copy {s}.cauc_evento_mudanca (data_referencia, data_referencia_anterior, tipo_ente,
                codigo_ibge, codigo_item, status_anterior, status_atual, validade_anterior, validade_atual, tipo_evento)
                from stdin""")) as cp:
            for e in eventos:
                cp.write_row((d, anterior_data, tipo, e.codigo_ibge, e.codigo_item, e.status_anterior, e.status_atual,
                              e.validade_anterior, e.validade_atual, e.tipo_evento))
        total += len(eventos)
    return total
