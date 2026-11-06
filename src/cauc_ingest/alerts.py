"""Etapa 09: alertas de negócio e operacionais, deduplicados. Só modo dry-run: grava em cauc_alerta e em arquivo.

ponytail: canais Email/Webhook não implementados; adicionar uma função `enviar_<canal>` quando canais e
responsáveis forem definidos (marcar enviado_em após confirmação).
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from psycopg.types.json import Jsonb

from .config import LINK_CONSULTA_OFICIAL
from .db import q

SEVERIDADE_ORDEM = {"CRITICA": 0, "ALTA": 1, "MEDIA": 2, "INFO": 3}


@dataclass
class Alerta:
    tipo: str
    severidade: str
    data_referencia: date | None
    id_entidade: str | None = None
    codigo_item: str | None = None
    payload: dict = field(default_factory=dict)
    sufixo_dedup: str | None = None

    @property
    def chave_dedup(self) -> str:
        ref = self.sufixo_dedup or (self.data_referencia.isoformat() if self.data_referencia else "-")
        return f"{self.tipo}|{self.id_entidade or '-'}|{self.codigo_item or '-'}|{ref}"


@dataclass
class ContextoNegocio:
    data_referencia: date
    eventos: list[dict]  # tipo_ente, codigo_ibge, codigo_item, tipo_evento
    apto_atual: dict[tuple[str, str], bool]
    apto_anterior: dict[tuple[str, str], bool]
    pendentes_atuais: dict[tuple[str, str], list[str]]
    persistentes: list[tuple[str, str, str, date]]  # tipo, ibge, item, pendente desde
    entidades: dict[tuple[str, str], list[tuple[str, str]]]  # (tipo, ibge) -> [(id_entidade, nome)]
    itens_criticos: set[str]
    itens_suprimidos: set[str] = field(default_factory=set)
    silencios: list[tuple[str | None, str | None, str | None]] = field(default_factory=list)  # (entidade, item, tipo)
    primeira_carga: bool = False
    vencimentos: list[tuple[str, str, str, int]] = field(default_factory=list)  # tipo, ibge, item, dias (se ativo)
    tipo_ente: str = ""


def _silenciado(a: Alerta, silencios) -> bool:
    return any((e is None or e == a.id_entidade) and (i is None or i == a.codigo_item) and (t is None or t == a.tipo)
               for e, i, t in silencios)


def gerar_alertas_negocio(ctx: ContextoNegocio) -> list[Alerta]:
    if ctx.primeira_carga:
        return [Alerta("RESUMO_LINHA_DE_BASE", "INFO", ctx.data_referencia,
                       sufixo_dedup=f"{ctx.tipo_ente}|{ctx.data_referencia.isoformat()}",
                       payload={"tipo_ente": ctx.tipo_ente, "entes_monitorados": sum(len(v) for v in ctx.entidades.values()),
                                "inaptos": sum(len(ctx.entidades.get(k, [])) for k, v in ctx.apto_atual.items() if not v)})]
    out: list[Alerta] = []

    def para_entidades(chave, tipo, sev, item=None, payload=None, sufixo=None):
        for id_ent, nome in ctx.entidades.get(chave, []):
            out.append(Alerta(tipo, sev, ctx.data_referencia, id_ent, item,
                              {"nome": nome, "tipo_ente": chave[0], "codigo_ibge": chave[1], **(payload or {})}, sufixo))

    for ev in ctx.eventos:
        chave, item = (ev["tipo_ente"], ev["codigo_ibge"]), ev["codigo_item"]
        if item in ctx.itens_suprimidos:
            continue
        if ev["tipo_evento"] == "NOVA_PENDENCIA" and item in ctx.itens_criticos:
            para_entidades(chave, "NOVA_PENDENCIA", "ALTA", item)
        elif ev["tipo_evento"] == "PENDENCIA_RESOLVIDA":
            para_entidades(chave, "PENDENCIA_RESOLVIDA", "INFO", item)
    for chave, apto in ctx.apto_atual.items():
        antes = ctx.apto_anterior.get(chave)
        if antes is None or antes == apto:
            continue
        pend = ctx.pendentes_atuais.get(chave, [])
        if set(pend) & ctx.itens_suprimidos and not apto:
            continue
        para_entidades(chave, "RECUPEROU_APTIDAO" if apto else "PERDEU_APTIDAO", "INFO" if apto else "CRITICA",
                       payload={"pendencias": pend})
    for tipo, ibge, item, desde in ctx.persistentes:
        semanas = (ctx.data_referencia - desde).days // 7
        if semanas >= 4 and item not in ctx.itens_suprimidos:
            para_entidades((tipo, ibge), "PENDENCIA_PERSISTENTE", "MEDIA", item, {"desde": desde.isoformat(), "semanas": semanas},
                           sufixo=f"desde={desde.isoformat()}|bloco={semanas // 4}")
    for tipo, ibge, item, dias in ctx.vencimentos:
        if item not in ctx.itens_suprimidos:
            para_entidades((tipo, ibge), "VENCIMENTO_PROXIMO", "MEDIA", item, {"dias_para_vencer": dias})
    return [a for a in out if not _silenciado(a, ctx.silencios)]


def compor_mensagem(alertas: list[Alerta], data_ref: date | None, descricoes: dict[str, str], vereditos: dict[str, str]) -> str:
    """Uma mensagem por execução, agrupada por entidade e severidade (sem destinatários definidos ainda)."""
    semana = data_ref.strftime("%G-W%V") if data_ref else "-"
    por_entidade = defaultdict(list)
    for a in alertas:
        if a.id_entidade:
            por_entidade[a.id_entidade].append(a)
    linhas = [f"[CAUC] Situação em {data_ref:%d/%m/%Y} (semana {semana}) · {len(por_entidade)} entidades com mudança"
              if data_ref else "[CAUC] Alertas operacionais", ""]
    for id_ent, lista in sorted(por_entidade.items(), key=lambda kv: min(SEVERIDADE_ORDEM[a.severidade] for a in kv[1])):
        nome = lista[0].payload.get("nome", id_ent)
        linhas.append(f"{nome} ({id_ent})")
        for a in sorted(lista, key=lambda a: SEVERIDADE_ORDEM[a.severidade]):
            item = f" {a.codigo_item} {descricoes.get(a.codigo_item, '')}" if a.codigo_item else ""
            extra = f" · pendências atuais: {', '.join(a.payload['pendencias'])}" if a.payload.get("pendencias") else ""
            linhas.append(f"  {a.severidade} · {a.tipo}{item}{extra}")
        linhas.append("")
    eventos = defaultdict(int)
    for a in alertas:
        eventos[a.tipo] += 1
    linhas.append("Resumo: " + (" · ".join(f"{n} {t}" for t, n in sorted(eventos.items())) or "sem alertas"))
    for a in alertas:
        if not a.id_entidade:
            linhas.append(f"OPERACIONAL · {a.severidade} · {a.tipo}: {json.dumps(a.payload, ensure_ascii=False, default=str)}")
    if vereditos:
        linhas.append("Qualidade da carga: " + "; ".join(f"{t} {v.replace('_', ' ')}" for t, v in vereditos.items()))
    linhas += [f"Consulta oficial: {LINK_CONSULTA_OFICIAL}",
               "A fonte oficial (CAUC/Tesouro Nacional) prevalece sobre esta mensagem."]
    return "\n".join(linhas) + "\n"


def registrar(conn, run_id, alertas: list[Alerta]) -> list[Alerta]:
    """Grava antes de qualquer envio; devolve só os inéditos (chave_dedup nova)."""
    novos = []
    for a in alertas:
        linha = conn.execute(q("""insert into {s}.cauc_alerta (run_id, chave_dedup, tipo, severidade, id_entidade_sistema,
                                  data_referencia, payload, canal) values (%s, %s, %s, %s, %s, %s, %s, 'dry-run')
                                  on conflict (chave_dedup) do nothing returning id"""),
                             (run_id, a.chave_dedup, a.tipo, a.severidade, a.id_entidade, a.data_referencia,
                              Jsonb(a.payload, dumps=lambda o: json.dumps(o, default=str)))).fetchone()
        if linha:
            novos.append(a)
    return novos


def gravar_mensagem(pasta: Path, run_id, texto: str) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"alertas_{run_id}.txt"
    caminho.write_text(texto, encoding="utf-8")
    return caminho


SQL_CONTEXTO_RESUMO = """
select tipo_ente, codigo_ibge, apto_transferencia, itens_pendentes from {s}.cauc_vw_resumo_ente
where tipo_ente = %s and data_referencia = %s
"""

SQL_ENTIDADES_MONITORADAS = """
select m.tipo_ente, m.codigo_ibge, m.id_entidade_sistema, coalesce(en.nome, m.id_entidade_sistema)
from {s}.cauc_map_entidade m
join transferepro.entities en on en.identif_proponente = m.id_entidade_sistema
where m.validado_por is not null and m.tipo_ente = %s
  and exists (select 1 from transferepro.monitored_entities me
              where me.identif_proponente = m.id_entidade_sistema and me.ativo and me.deleted_at is null)
"""


def contexto_negocio(conn, tipo: str, data_ref: date, data_ant: date | None, itens_suprimidos: set[str],
                     janela_vencimento: int, vencimento_ativo: bool) -> ContextoNegocio:
    def resumo(d):
        return {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(q(SQL_CONTEXTO_RESUMO), (tipo, d))} if d else {}
    atual, anterior = resumo(data_ref), resumo(data_ant)
    entidades = defaultdict(list)
    for t, ibge, ident, nome in conn.execute(q(SQL_ENTIDADES_MONITORADAS), (tipo,)):
        entidades[(t, ibge)].append((ident, nome))
    eventos = [dict(zip(("tipo_ente", "codigo_ibge", "codigo_item", "tipo_evento"), r)) for r in conn.execute(q(
        "select tipo_ente, codigo_ibge, codigo_item, tipo_evento from {s}.cauc_evento_mudanca where tipo_ente = %s and data_referencia = %s"),
        (tipo, data_ref))]
    persistentes = list(conn.execute(q("""select tipo_ente, codigo_ibge, codigo_item, valido_de from {s}.cauc_hist_situacao_item
                                          where tipo_ente = %s and atual and status = 'PENDENTE'"""), (tipo,)))
    vencimentos = []
    if vencimento_ativo:
        vencimentos = list(conn.execute(q("""select tipo_ente, codigo_ibge, codigo_item, dias_para_vencer
            from {s}.cauc_fato_situacao_item where tipo_ente = %s and data_referencia = %s and status = 'REGULAR'
              and dias_para_vencer between 1 and %s"""), (tipo, data_ref, janela_vencimento)))
    criticos = {r[0] for r in conn.execute(q("select codigo_item from {s}.cauc_dim_item where critico_negocio"))}
    silencios = list(conn.execute(q("""select id_entidade_sistema, codigo_item, tipo from {s}.cauc_alerta_silencio
                                       where ate >= current_date""")))
    return ContextoNegocio(data_ref, eventos, {k: v[0] for k, v in atual.items()}, {k: v[0] for k, v in anterior.items()},
                           {k: list(v[1]) for k, v in atual.items()}, persistentes, dict(entidades), criticos,
                           itens_suprimidos, silencios, data_ant is None, vencimentos, tipo)
