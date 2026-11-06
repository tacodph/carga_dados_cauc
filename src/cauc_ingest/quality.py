"""Etapa 08: regras de conteúdo, drift e reconciliação; métricas; veredito; relatório."""

import json
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .config import UF_IBGE, UF_REGIAO
from .normalize import Fato
from .parse import ArquivoCauc
from .validate import AVISO, BLOQUEANTE, QUARENTENA, Resultado, colunas_itens, falhas

# Limites ajustáveis sem mudar código: CAUC_DQ_LIMITES_JSON aponta para um JSON com as chaves a sobrescrever.
LIMITES = {
    "dominio_quarentena_pct": 0.0,
    "dominio_bloqueante_pct": 0.5,
    "validade_distante_dias": 400,
    "drift_qtd_entes_municipio": 5,
    "drift_qtd_entes_estado": 0,
    "drift_taxa_semanas_historico": 12,
    "drift_taxa_semanas_minimas": 8,
    "drift_taxa_zscore": 4.0,
    "drift_taxa_pp_absoluto": 15.0,
    "atraso_data_ref_dias": 10,
}


def limites(caminho_json: str = "") -> dict:
    return {**LIMITES, **(json.loads(Path(caminho_json).read_text(encoding="utf-8")) if caminho_json else {})}


@dataclass
class Anterior:
    """Versão publicada anterior do mesmo tipo de ente."""
    data_referencia: date
    entes: dict[str, dict]  # ibge -> atributos
    status: dict[tuple[str, str], str]  # (ibge, item) -> status


def _r(regra, sev, ok, msg, **kw) -> Resultado:
    return Resultado(regra, sev, ok, "" if ok else msg, **kw)


def pct_pendente_por_item(fatos: list[Fato]) -> dict[str, float]:
    total, pend = Counter(), Counter()
    for f in fatos:
        total[f.codigo_item] += 1
        pend[f.codigo_item] += f.status == "PENDENTE"
    return {i: 100.0 * pend[i] / total[i] for i in total}


def regras_conteudo(tipo: str, data_ref: date, entes: list[dict], fatos: list[Fato], lim: dict) -> list[Resultado]:
    res = []
    desconhecidos = [f for f in fatos if f.status == "DESCONHECIDO"]
    pct = 100.0 * len(desconhecidos) / max(len(fatos), 1)
    sev = BLOQUEANTE if pct > lim["dominio_bloqueante_pct"] else QUARENTENA
    res.append(_r("VAL_DOMINIO_ITEM", sev, pct <= lim["dominio_quarentena_pct"],
                  f"{len(desconhecidos)} célula(s) fora do domínio (data dd/mm/aa, '!', 'Desabilitado'): {pct:.3f}%",
                  valor_observado=pct, limite=f">{lim['dominio_quarentena_pct']}% quarentena; >{lim['dominio_bloqueante_pct']}% bloqueia",
                  detalhe={"exemplos": [f"{f.codigo_ibge} {f.codigo_item}={f.valor_bruto!r}" for f in desconhecidos[:10]]}))
    passadas = [f for f in fatos if f.status == "REGULAR" and f.data_validade < data_ref]
    res.append(_r("VAL_VALIDADE_PASSADA", QUARENTENA, not passadas,
                  f"{len(passadas)} item(ns) REGULAR com validade anterior à data de referência",
                  valor_observado=len(passadas),
                  detalhe={"exemplos": [f"{f.codigo_ibge} {f.codigo_item}={f.valor_bruto}" for f in passadas[:10]]}))
    teto = data_ref + timedelta(days=lim["validade_distante_dias"])
    distantes = [f for f in fatos if f.status == "REGULAR" and f.data_validade > teto]
    res.append(_r("VAL_VALIDADE_DISTANTE", AVISO, not distantes,
                  f"{len(distantes)} validade(s) a mais de {lim['validade_distante_dias']} dias (possível erro de século)",
                  valor_observado=len(distantes),
                  detalhe={"exemplos": [f"{f.codigo_ibge} {f.codigo_item}={f.valor_bruto}" for f in distantes[:10]]}))
    regiao_ruim = [e for e in entes if UF_REGIAO.get(e["uf"]) != e["regiao"]]
    res.append(_r("VAL_UF_REGIAO", QUARENTENA, not regiao_ruim, f"{len(regiao_ruim)} ente(s) com região incoerente com a UF",
                  valor_observado=len(regiao_ruim),
                  detalhe={"exemplos": [f"{e['codigo_ibge']} {e['uf']}/{e['regiao']}" for e in regiao_ruim[:10]]}))
    if tipo == "MUNICIPIO":
        ibge_ruim = [e for e in entes if UF_IBGE.get(e["uf"]) != e["codigo_ibge"][:2]]
    else:
        ibge_ruim = [e for e in entes if UF_IBGE.get(e["uf"]) != e["codigo_ibge"]]
    res.append(_r("VAL_IBGE_UF", BLOQUEANTE, not ibge_ruim, f"{len(ibge_ruim)} ente(s) com prefixo IBGE incoerente com a UF",
                  valor_observado=len(ibge_ruim),
                  detalhe={"exemplos": [f"{e['codigo_ibge']} {e['uf']}" for e in ibge_ruim[:10]]}))
    pop_ruim = [e for e in entes if (e["populacao"] is None or e["populacao"] <= 0) and e["codigo_ibge"] != "53"]
    res.append(_r("VAL_POPULACAO", AVISO, not pop_ruim, f"{len(pop_ruim)} ente(s) com população nula ou ≤ 0",
                  valor_observado=len(pop_ruim), detalhe={"exemplos": [e["codigo_ibge"] for e in pop_ruim[:10]]}))
    sem_nome = [e["codigo_ibge"] for e in entes if not e["nome"]]
    res.append(_r("VAL_NOME_VAZIO", BLOQUEANTE, not sem_nome, f"{len(sem_nome)} ente(s) sem nome",
                  valor_observado=len(sem_nome), detalhe={"exemplos": sem_nome[:10]}))
    return res


def regras_drift(tipo: str, data_ref: date, hoje: date, entes: list[dict], fatos: list[Fato],
                 anterior: Anterior | None, historico_taxas: dict[str, list[float]], lim: dict) -> list[Resultado]:
    """`historico_taxas`: item -> % pendente das versões anteriores (mais recente primeiro)."""
    atraso = (hoje - data_ref).days
    res = [_r("DRIFT_DATA_REF_ATRASO", AVISO, atraso <= lim["atraso_data_ref_dias"],
              f"Data de referência {data_ref:%d/%m/%Y} tem {atraso} dias", valor_observado=atraso)]
    if anterior is None:
        return res  # primeira carga: comparação não se aplica
    n, n_ant = len(entes), len(anterior.entes)
    lim_qtd = lim["drift_qtd_entes_municipio" if tipo == "MUNICIPIO" else "drift_qtd_entes_estado"]
    res.append(_r("DRIFT_QTD_ENTES", QUARENTENA, abs(n - n_ant) <= lim_qtd,
                  f"Nº de entes passou de {n_ant} para {n}", valor_observado=n - n_ant, limite=f"±{lim_qtd}"))
    atuais = {e["codigo_ibge"]: e for e in entes}
    ausentes = sorted(set(anterior.entes) - set(atuais))
    res.append(_r("DRIFT_ENTES_AUSENTES", QUARENTENA, not ausentes, f"{len(ausentes)} ente(s) sumiram do arquivo",
                  valor_observado=len(ausentes),
                  detalhe={"exemplos": [f"{i} {anterior.entes[i]['nome']}/{anterior.entes[i]['uf']}" for i in ausentes[:10]]}))
    troca = [i for i, e in atuais.items() if i in anterior.entes and anterior.entes[i]["codigo_siafi"] != e["codigo_siafi"]]
    res.append(_r("DRIFT_TROCA_SIAFI", QUARENTENA, not troca, f"{len(troca)} ente(s) trocaram de código SIAFI",
                  valor_observado=len(troca),
                  detalhe={"exemplos": [f"{i}: {anterior.entes[i]['codigo_siafi']} → {atuais[i]['codigo_siafi']}" for i in troca[:10]]}))

    taxas = pct_pendente_por_item(fatos)
    taxas_ant = pct_pendente_por_item([Fato(i, it, "", s, None, None) for (i, it), s in anterior.status.items()])
    instaveis = {}
    for item, x in taxas.items():
        serie = historico_taxas.get(item, [])[: lim["drift_taxa_semanas_historico"]]
        if len(serie) >= lim["drift_taxa_semanas_minimas"]:
            med = statistics.median(serie)
            mad = statistics.median(abs(v - med) for v in serie)
            z = 0.6745 * abs(x - med) / mad if mad else (0.0 if abs(x - med) <= lim["drift_taxa_pp_absoluto"] else float("inf"))
            if z > lim["drift_taxa_zscore"]:
                instaveis[item] = f"{x:.1f}% (mediana {med:.1f}%, z={z:.1f})"
        elif item in taxas_ant and abs(x - taxas_ant[item]) > lim["drift_taxa_pp_absoluto"]:
            instaveis[item] = f"{taxas_ant[item]:.1f}% → {x:.1f}%"
    res.append(_r("DRIFT_TAXA_PENDENCIA_ITEM", QUARENTENA, not instaveis,
                  "Taxa de pendência fora do padrão: " + "; ".join(f"{i}: {v}" for i, v in instaveis.items()),
                  valor_observado=len(instaveis), detalhe={"itens": sorted(instaveis)}))

    def uniforme(pares) -> dict[str, str]:
        por_item = defaultdict(set)
        for item, status in pares:
            por_item[item].add(status)
        return {i: s.pop() for i, s in por_item.items() if len(s) == 1 and next(iter(s)) in ("PENDENTE", "NAO_AVALIADO")}

    uni = uniforme((f.codigo_item, f.status) for f in fatos)
    uni_ant = uniforme((it, s) for (_, it), s in anterior.status.items())
    entrou = sorted(i for i, s in uni.items() if uni_ant.get(i) != s)
    saiu = sorted(i for i in uni_ant if i not in uni and i in taxas)
    res.append(_r("DRIFT_ITEM_TUDO_IGUAL", QUARENTENA, not entrou,
                  "Item passou a ter 100% do mesmo valor (falha na fonte do item): "
                  + ", ".join(f"{i}={uni[i]}" for i in entrou), detalhe={"itens": entrou}))
    res.append(_r("DRIFT_ITEM_VOLTOU_A_VARIAR", AVISO, not saiu,
                  "Item deixou de ser 100% igual e voltou a ser avaliado (afeta a aptidão de muitos entes): " + ", ".join(saiu),
                  detalhe={"itens": saiu}))
    return res


def reconciliar(arq: ArquivoCauc, qtd_bronze: int, entes: list[dict], fatos: list[Fato]) -> list[Resultado]:
    """Bug do pipeline, não da fonte: tudo BLOQUEANTE."""
    n_itens = len(colunas_itens(arq.cabecalho))
    res = [
        _r("REC_LINHAS", BLOQUEANTE, len(arq.linhas) == qtd_bronze == len(entes),
           f"Arquivo {len(arq.linhas)} linhas, bronze {qtd_bronze}, silver {len(entes)} entes"),
        _r("REC_REGISTROS", BLOQUEANTE, len(fatos) == len(entes) * n_itens,
           f"Silver {len(fatos)} registros ≠ {len(entes)} entes × {n_itens} itens"),
    ]
    # recontagem independente direto das células brutas
    bruto = Counter()
    for _, cel in arq.linhas:
        for v in cel[7:]:
            v = v.strip()
            bruto["PENDENTE" if v == "!" else "NAO_AVALIADO" if v.lower() == "desabilitado"
                  else "REGULAR" if re.fullmatch(r"\d{2}/\d{2}/\d{2}", v) else "OUTRO"] += 1
    silver = Counter(f.status for f in fatos)
    ok = all(bruto[s] == silver[s] for s in ("PENDENTE", "NAO_AVALIADO")) and \
        bruto["REGULAR"] + bruto["OUTRO"] == silver["REGULAR"] + silver["DESCONHECIDO"]
    res.append(_r("REC_STATUS", BLOQUEANTE, ok, f"Contagem por status diverge: bruto {dict(bruto)} × silver {dict(silver)}"))
    return res


def veredito(resultados: list[Resultado]) -> str:
    if falhas(resultados, BLOQUEANTE):
        return "REJEITADO"
    if falhas(resultados, QUARENTENA):
        return "COM_RESSALVA"
    return "PUBLICADO"


def metricas(entes: list[dict], fatos: list[Fato], tamanho_bytes: int) -> list[tuple[str, str, float]]:
    """(metrica, dimensao, valor)."""
    com_pend = {f.codigo_ibge for f in fatos if f.status == "PENDENTE"}
    out = [("qtd_entes", "", len(entes)), ("bytes_arquivo", "", tamanho_bytes),
           ("pct_entes_com_pendencia", "", 100.0 * len(com_pend) / max(len(entes), 1))]
    out += [("pct_pendente_item", i, v) for i, v in sorted(pct_pendente_por_item(fatos).items())]
    por_uf, pend_uf = Counter(), Counter()
    for e in entes:
        por_uf[e["uf"]] += 1
        pend_uf[e["uf"]] += e["codigo_ibge"] in com_pend
    out += [("pct_entes_com_pendencia_uf", uf, 100.0 * pend_uf[uf] / por_uf[uf]) for uf in sorted(por_uf)]
    for s, n in Counter(f.status for f in fatos).items():
        out.append(("qtd_status", s, n))
    return out


def relatorio_md(tipo: str, data_ref: date, decisao: str, resultados: list[Resultado],
                 met: list[tuple[str, str, float]], met_ant: dict[tuple[str, str], float]) -> str:
    rotulo = {"PUBLICADO": "PUBLICADO", "COM_RESSALVA": "COM RESSALVA", "REJEITADO": "REJEITADO"}[decisao]
    linhas = [f"# Qualidade CAUC · {tipo} · {data_ref:%d/%m/%Y}", "", f"**Veredito: {rotulo}**", "",
              "## Regras que falharam", ""]
    fal = falhas(resultados)
    if not fal:
        linhas.append("Nenhuma.")
    for r in fal:
        linhas.append(f"- **{r.regra}** ({r.severidade}): {r.mensagem}")
        for ex in r.detalhe.get("exemplos", [])[:10]:
            linhas.append(f"  - {ex}")
    linhas += ["", "## Métricas principais", "", "| Métrica | Atual | Anterior |", "|---|---|---|"]
    for m, d, v in met:
        if m in ("qtd_entes", "pct_entes_com_pendencia", "qtd_status", "pct_pendente_item"):
            ant = met_ant.get((m, d))
            nome = f"{m} {d}".strip()
            linhas.append(f"| {nome} | {v:,.2f} | {'' if ant is None else f'{ant:,.2f}'} |")
    return "\n".join(linhas) + "\n"
