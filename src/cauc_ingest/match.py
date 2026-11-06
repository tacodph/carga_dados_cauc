"""Etapa 07: entidades do sistema (transferepro.entities, chave CNPJ) -> ente CAUC, em cascata."""

import csv
import difflib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .config import SICONFI_ENTES_URL
from .db import q
from .normalize import normalizar_nome
from .validate import AVISO, QUARENTENA, Resultado

log = logging.getLogger(__name__)

# metodo -> (confiança, validação automática)
METODOS = {
    "IBGE": (1.00, True),
    "SIAFI": (1.00, True),
    "CNPJ_VIA_TABELA": (0.98, True),  # tabela oficial Siconfi (CNPJ principal do ente)
    "ORGAO_VIA_IBGE6": (0.95, True),  # órgão municipal (natureza jurídica) herda a situação do município
    "ORGAO_VIA_UF": (0.95, True),     # órgão estadual herda a situação do estado
    "NOME_UF": (0.90, False),
    "NOME_UF_FUZZY": (0.70, False),   # só sugestão
}
PREFIXOS_NOME = ("PREFEITURA MUNICIPAL DE ", "PREFEITURA MUNICIPAL DO ", "PREFEITURA DE ", "MUNICIPIO DE ",
                 "MUNICIPIO DO ", "MUNICIPIO DA ")


@dataclass
class Entidade:
    id: str  # CNPJ
    uf: str | None = None
    nome_municipio: str | None = None
    ibge: str | None = None  # 7 dígitos (município) ou 6 (cod_municipio_6dig)
    siafi: str | None = None
    natureza: str | None = None  # MUNICIPAL | ESTADUAL | None (não é órgão de ente federado)


@dataclass
class EnteCauc:
    tipo_ente: str
    codigo_ibge: str
    codigo_siafi: str
    uf: str
    nome_normalizado: str


@dataclass
class Match:
    id_entidade: str
    tipo_ente: str
    codigo_ibge: str
    metodo: str
    confianca: float
    automatico: bool


def nome_chave(nome: str) -> str:
    n = normalizar_nome(nome).replace("-", " ").replace("'", " ")
    for p in PREFIXOS_NOME:
        if n.startswith(p):
            n = n[len(p):]
    return " ".join(n.split())


def cnpj_valido(cnpj: str) -> bool:
    d = re.sub(r"\D", "", cnpj or "")
    if len(d) != 14 or d == d[0] * 14:
        return False
    for tam in (12, 13):
        pesos = list(range(tam - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(int(a) * p for a, p in zip(d[:tam], pesos))
        if int(d[tam]) != (0 if soma % 11 < 2 else 11 - soma % 11):
            return False
    return True


def casar(entidades: list[Entidade], entes: list[EnteCauc], ref_cnpj: dict[str, tuple[str, str]]) -> list[Match]:
    """Cascata do mais forte ao mais fraco. Nome nunca casa sem UF."""
    mun = {e.codigo_ibge: e for e in entes if e.tipo_ente == "MUNICIPIO"}
    mun6 = {i[:6]: e for i, e in mun.items()}
    est_uf = {e.uf: e for e in entes if e.tipo_ente == "ESTADO"}
    por_siafi = {e.codigo_siafi: e for e in entes if e.tipo_ente == "MUNICIPIO"}
    por_nome_uf: dict[tuple[str, str], list[EnteCauc]] = {}
    for e in mun.values():
        por_nome_uf.setdefault((e.uf, nome_chave(e.nome_normalizado)), []).append(e)

    def m(ent, ente, metodo):
        conf, auto = METODOS[metodo]
        return Match(ent.id, ente.tipo_ente, ente.codigo_ibge, metodo, conf, auto)

    saida = []
    for ent in entidades:
        ibge = (ent.ibge or "").strip()
        if len(ibge) == 7 and ibge in mun:
            saida.append(m(ent, mun[ibge], "IBGE"))
        elif ent.siafi and ent.siafi.strip() in por_siafi:
            saida.append(m(ent, por_siafi[ent.siafi.strip()], "SIAFI"))
        elif ent.id in ref_cnpj:
            tipo, cod = ref_cnpj[ent.id]
            alvo = mun.get(cod) if tipo == "MUNICIPIO" else next((e for e in est_uf.values() if e.codigo_ibge == cod), None)
            if alvo:
                saida.append(m(ent, alvo, "CNPJ_VIA_TABELA"))
        elif ent.natureza == "MUNICIPAL" and ibge[:6] in mun6 and (not ent.uf or mun6[ibge[:6]].uf == ent.uf):
            saida.append(m(ent, mun6[ibge[:6]], "ORGAO_VIA_IBGE6"))
        elif ent.natureza == "ESTADUAL" and ent.uf in est_uf:
            saida.append(m(ent, est_uf[ent.uf], "ORGAO_VIA_UF"))
        elif ent.natureza == "MUNICIPAL" and ent.uf and ent.nome_municipio:
            chave = (ent.uf, nome_chave(ent.nome_municipio))
            exatos = por_nome_uf.get(chave, [])
            if len(exatos) == 1:
                saida.append(m(ent, exatos[0], "NOME_UF"))
                continue
            candidatos = [(difflib.SequenceMatcher(None, chave[1], n).ratio(), es)
                          for (uf, n), es in por_nome_uf.items() if uf == ent.uf and len(es) == 1]
            bons = [es[0] for r, es in candidatos if r >= 0.92]
            if len(bons) == 1:
                saida.append(m(ent, bons[0], "NOME_UF_FUZZY"))
    return saida


# ---------------------------------------------------------------- banco

def atualizar_ref_cnpj(conn, cliente: httpx.Client, max_idade_dias: int = 30) -> int | None:
    """Tabela CNPJ↔IBGE a partir da API de entes do Siconfi; atualiza no máximo 1x por mês."""
    ultima = conn.execute(q("select max(atualizado_em) from {s}.cauc_ref_cnpj_ente")).fetchone()[0]
    if ultima and datetime.now(timezone.utc) - ultima < timedelta(days=max_idade_dias):
        return None
    r = cliente.get(SICONFI_ENTES_URL)
    r.raise_for_status()
    linhas = []
    for it in r.json()["items"]:
        cnpj = re.sub(r"\D", "", str(it.get("cnpj") or "")).zfill(14)
        esfera = it.get("esfera")
        if esfera not in ("M", "E", "D") or not cnpj_valido(cnpj):
            continue
        tipo = "MUNICIPIO" if esfera == "M" else "ESTADO"
        cod = str(it["cod_ibge"]).zfill(7 if tipo == "MUNICIPIO" else 2)
        linhas.append((cnpj, tipo, cod, it.get("ente")))
    with conn.transaction():
        conn.execute(q("delete from {s}.cauc_ref_cnpj_ente where fonte = 'SICONFI'"))
        with conn.cursor() as cur:
            cur.executemany(q("""insert into {s}.cauc_ref_cnpj_ente (cnpj, tipo_ente, codigo_ibge, nome, fonte)
                                 values (%s, %s, %s, %s, 'SICONFI') on conflict (cnpj) do nothing"""), linhas)
    return len(linhas)


SQL_ENTIDADES = """
select en.identif_proponente, en.uf_proponente, en.municipio_proponente, en.cod_municipio_6dig,
       case nj.natureza_juridica when 'Administração Pública Municipal' then 'MUNICIPAL'
                                  when 'Administração Pública Estadual ou do Distrito Federal' then 'ESTADUAL' end
from transferepro.entities en
left join (
  select identif_proponente, mode() within group (order by natureza_juridica) as natureza_juridica
  from transfere_pro_transferegov.tab_propostas group by identif_proponente
) nj on nj.identif_proponente = en.identif_proponente
"""


def executar_match(conn, pasta_relatorio: Path, run_id) -> list[Resultado]:
    entidades = [Entidade(id=r[0], uf=r[1], nome_municipio=r[2], ibge=r[3], natureza=r[4])
                 for r in conn.execute(SQL_ENTIDADES)]
    entes = [EnteCauc(*r) for r in conn.execute(q("""select tipo_ente, codigo_ibge, codigo_siafi, uf, nome_normalizado
                                                      from {s}.cauc_dim_ente where atual"""))]
    ref = {r[0]: (r[1], r[2]) for r in conn.execute(q("select cnpj, tipo_ente, codigo_ibge from {s}.cauc_ref_cnpj_ente"))}
    existentes = {r[0]: r[1:] for r in conn.execute(q(
        "select id_entidade_sistema, tipo_ente, codigo_ibge, metodo_match, validado_por from {s}.cauc_map_entidade"))}
    matches = casar(entidades, entes, ref)
    novos = [m for m in matches if existentes.get(m.id_entidade, (None,) * 4)[:3] != (m.tipo_ente, m.codigo_ibge, m.metodo)]
    instaveis = [m for m in novos if m.id_entidade in existentes and existentes[m.id_entidade][3]
                 and existentes[m.id_entidade][:2] != (m.tipo_ente, m.codigo_ibge)]
    with conn.transaction(), conn.cursor() as cur:
        # nunca sobrescreve validação manual (validado_por diferente de AUTO)
        cur.executemany(q("""
            insert into {s}.cauc_map_entidade (id_entidade_sistema, tipo_ente, codigo_ibge, metodo_match, confianca,
                                               validado_por, validado_em)
            values (%s, %s, %s, %s, %s, %s, case when %s then now() end)
            on conflict (id_entidade_sistema) do update set
              tipo_ente = excluded.tipo_ente, codigo_ibge = excluded.codigo_ibge, metodo_match = excluded.metodo_match,
              confianca = excluded.confianca, validado_por = excluded.validado_por,
              validado_em = excluded.validado_em, atualizado_em = now()
            where cauc_map_entidade.validado_por is null or cauc_map_entidade.validado_por = 'AUTO'"""),
            [(m.id_entidade, m.tipo_ente, m.codigo_ibge, m.metodo, m.confianca, "AUTO" if m.automatico else None,
              m.automatico) for m in novos])

    casados = {m.id_entidade for m in matches}
    publicas = [e for e in entidades if e.natureza or e.id in ref]
    validados = {r[0] for r in conn.execute(q("select id_entidade_sistema from {s}.cauc_map_entidade where validado_por is not null"))}
    cobertura = 100.0 * sum(e.id in validados for e in publicas) / max(len(publicas), 1)
    ausentes = conn.execute(q("""select count(*) from {s}.cauc_map_entidade m
                                 where m.validado_por is not null and not exists (select 1 from {s}.cauc_vw_situacao_atual a
                                 where a.tipo_ente = m.tipo_ente and a.codigo_ibge = m.codigo_ibge)""")).fetchone()[0]

    pasta_relatorio.mkdir(parents=True, exist_ok=True)
    with open(pasta_relatorio / "relatorio_match.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["situacao", "id_entidade_sistema", "uf", "municipio", "natureza", "tipo_ente", "codigo_ibge", "metodo", "confianca"])
        por_id = {m.id_entidade: m for m in matches}
        for e in publicas:
            m = por_id.get(e.id)
            if m is None:
                w.writerow(["SEM_MATCH", e.id, e.uf, e.nome_municipio, e.natureza, "", "", "", ""])
            elif not m.automatico:
                w.writerow(["PENDENTE_VALIDACAO", e.id, e.uf, e.nome_municipio, e.natureza, m.tipo_ente, m.codigo_ibge, m.metodo, m.confianca])
        for m in novos:
            w.writerow(["NOVO_OU_ALTERADO", m.id_entidade, "", "", "", m.tipo_ente, m.codigo_ibge, m.metodo, m.confianca])
    log.info("match: %s entidades públicas, %s casadas, %s novas/alteradas, cobertura validada %.2f%%",
             len(publicas), len(casados & {e.id for e in publicas}), len(novos), cobertura)
    return [
        Resultado("MATCH_COBERTURA", AVISO, cobertura >= 99.0,
                  f"Cobertura de match validado {cobertura:.2f}% das entidades que são entes/órgãos públicos",
                  valor_observado=cobertura, limite=">= 99%"),
        Resultado("MATCH_INSTAVEL", QUARENTENA, not instaveis, f"{len(instaveis)} entidade(s) mudaram de ente",
                  valor_observado=len(instaveis), detalhe={"exemplos": [m.id_entidade for m in instaveis[:10]]}),
        Resultado("MATCH_ENTE_AUSENTE", QUARENTENA, not ausentes,
                  f"{ausentes} entidade(s) ligadas a ente que não existe mais no CAUC", valor_observado=ausentes),
    ]
