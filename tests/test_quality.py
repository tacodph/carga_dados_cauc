import dataclasses
from datetime import date

import pytest

from cauc_ingest import normalize, parse, quality
from cauc_ingest.validate import falhas

REF = date(2026, 10, 30)
LIM = quality.LIMITES


@pytest.fixture(scope="module")
def mun(bytes_municipios):
    arq = parse.ler(bytes_municipios)
    entes, fatos = normalize.normalizar(arq)
    return arq, entes, fatos


def anterior_de(entes, fatos):
    return quality.Anterior(date(2026, 10, 23), {e["codigo_ibge"]: e for e in entes},
                            {(f.codigo_ibge, f.codigo_item): f.status for f in fatos})


def regras(res):
    return {r.regra: r for r in falhas(res)}


def test_arquivo_real_publicado(mun):
    arq, entes, fatos = mun
    res = (quality.regras_conteudo("MUNICIPIO", REF, entes, fatos, LIM)
           + quality.regras_drift("MUNICIPIO", REF, REF, entes, fatos, None, {}, LIM)
           + quality.reconciliar(arq, len(arq.linhas), entes, fatos))
    assert quality.veredito(res) == "PUBLICADO"
    assert {r.severidade for r in falhas(res)} <= {"AVISO"}


def test_mesma_versao_como_anterior_nao_gera_drift(mun):
    _, entes, fatos = mun
    res = quality.regras_drift("MUNICIPIO", REF, REF, entes, fatos, anterior_de(entes, fatos), {}, LIM)
    assert falhas(res) == []


def test_dominio_quarentena_e_bloqueio(mun):
    _, entes, fatos = mun
    um = [dataclasses.replace(fatos[0], status="DESCONHECIDO", valor_bruto="X")] + fatos[1:]
    assert regras(quality.regras_conteudo("MUNICIPIO", REF, entes, um, LIM))["VAL_DOMINIO_ITEM"].severidade == "QUARENTENA"
    muitos = [dataclasses.replace(f, status="DESCONHECIDO") if i < 1000 else f for i, f in enumerate(fatos)]
    assert regras(quality.regras_conteudo("MUNICIPIO", REF, entes, muitos, LIM))["VAL_DOMINIO_ITEM"].severidade == "BLOQUEANTE"


def test_regras_de_conteudo_falham(mun):
    _, entes, fatos = mun
    e = [dict(x) for x in entes]
    e[0]["regiao"], e[1]["uf"], e[2]["nome"], e[3]["populacao"] = "S", "SP", "", 0
    f = list(fatos)
    f[0] = dataclasses.replace(f[0], status="REGULAR", data_validade=date(2026, 10, 1))
    f[1] = dataclasses.replace(f[1], status="REGULAR", data_validade=date(2126, 10, 1))
    r = regras(quality.regras_conteudo("MUNICIPIO", REF, e, f, LIM))
    assert {"VAL_UF_REGIAO", "VAL_IBGE_UF", "VAL_NOME_VAZIO", "VAL_POPULACAO", "VAL_VALIDADE_PASSADA",
            "VAL_VALIDADE_DISTANTE"} <= set(r)
    assert quality.veredito(list(r.values())) == "REJEITADO"


def test_drift_entes_siafi_e_atraso(mun):
    _, entes, fatos = mun
    ant = anterior_de(entes, fatos)
    e = [dict(x) for x in entes[10:]]
    e[0]["codigo_siafi"] = "9999"
    f = [x for x in fatos if x.codigo_ibge in {y["codigo_ibge"] for y in e}]
    r = regras(quality.regras_drift("MUNICIPIO", REF, date(2026, 11, 20), e, f, ant, {}, LIM))
    assert {"DRIFT_QTD_ENTES", "DRIFT_ENTES_AUSENTES", "DRIFT_TROCA_SIAFI", "DRIFT_DATA_REF_ATRASO"} <= set(r)


def test_drift_taxa_absoluta_e_item_tudo_igual(mun):
    _, entes, fatos = mun
    ant = anterior_de(entes, fatos)
    f = [dataclasses.replace(x, status="PENDENTE") if x.codigo_item == "5.1" else x for x in fatos]
    r = regras(quality.regras_drift("MUNICIPIO", REF, REF, entes, f, ant, {}, LIM))
    assert r["DRIFT_TAXA_PENDENCIA_ITEM"].detalhe["itens"] == ["5.1"]
    assert r["DRIFT_ITEM_TUDO_IGUAL"].detalhe["itens"] == ["5.1"]
    assert "1.3" not in r["DRIFT_ITEM_TUDO_IGUAL"].detalhe["itens"]  # 100% Desabilitado já era o estado conhecido


def test_drift_taxa_zscore_com_historico(mun):
    _, entes, fatos = mun
    taxas = quality.pct_pendente_por_item(fatos)
    hist = {i: [v + d for d in (0.1, -0.1, 0.2, -0.2, 0.0, 0.1, -0.1, 0.05)] for i, v in taxas.items()}
    hist["3.2.3"] = [taxas["3.2.3"] - 5 + d for d in (0.1, -0.1, 0.2, -0.2, 0.0, 0.1, -0.1, 0.05)]
    r = regras(quality.regras_drift("MUNICIPIO", REF, REF, entes, fatos, anterior_de(entes, fatos), hist, LIM))
    assert r["DRIFT_TAXA_PENDENCIA_ITEM"].detalhe["itens"] == ["3.2.3"]


def test_item_voltou_a_variar_e_aviso(mun):
    _, entes, fatos = mun
    ant = anterior_de(entes, fatos)
    f = [dataclasses.replace(x, status="REGULAR", data_validade=REF) if x.codigo_item == "1.3" and x.codigo_ibge < "2" else x
         for x in fatos]
    r = regras(quality.regras_drift("MUNICIPIO", REF, REF, entes, f, ant, {}, LIM))
    assert r["DRIFT_ITEM_VOLTOU_A_VARIAR"].severidade == "AVISO"


def test_reconciliacao_detecta_bug(mun):
    arq, entes, fatos = mun
    r = regras(quality.reconciliar(arq, len(arq.linhas) - 1, entes, fatos[:-1]))
    assert {"REC_LINHAS", "REC_REGISTROS", "REC_STATUS"} <= set(r)


def test_limites_por_json(tmp_path):
    p = tmp_path / "lim.json"
    p.write_text('{"drift_taxa_pp_absoluto": 30}', encoding="utf-8")
    assert quality.limites(str(p))["drift_taxa_pp_absoluto"] == 30


def test_relatorio_md(mun):
    _, entes, fatos = mun
    met = quality.metricas(entes, fatos, 123)
    texto = quality.relatorio_md("MUNICIPIO", REF, "PUBLICADO", [], met, {})
    assert "Veredito: PUBLICADO" in texto and "pct_pendente_item 3.2.3" in texto
