from datetime import date

import pytest

from cauc_ingest import parse, validate
from conftest import variante


def bloqueios(conteudo, tipo, dim):
    arq = parse.ler(conteudo)
    return {r.regra: r.mensagem for r in validate.falhas(validate.validar_estrutura(arq, tipo, dim), validate.BLOQUEANTE)}


@pytest.mark.parametrize("fixture,tipo,linhas,itens", [
    ("bytes_municipios", "MUNICIPIO", 5569, 28),
    ("bytes_estados", "ESTADO", 27, 30),
])
def test_arquivos_reais_passam(request, dim_item, fixture, tipo, linhas, itens):
    arq = parse.ler(request.getfixturevalue(fixture))
    assert arq.data_referencia == date(2026, 10, 30)
    assert arq.tipo_ente == tipo
    assert arq.encoding == "latin-1"
    assert len(arq.linhas) == linhas
    assert len(arq.legenda) == 39
    assert len(validate.colunas_itens(arq.cabecalho)) == itens
    assert validate.falhas(validate.validar_estrutura(arq, tipo, dim_item)) == []


def test_acentos_preservados(bytes_municipios, bytes_estados):
    nomes = {c[1] for _, c in parse.ler(bytes_municipios).linhas} | {c[1] for _, c in parse.ler(bytes_estados).linhas}
    assert {"Acrelândia", "Brasiléia", "Amapá", "São Paulo"} <= nomes


def test_legenda_vira_30_itens(bytes_estados, dim_item):
    itens = parse.itens_da_legenda(parse.ler(bytes_estados).legenda)
    assert set(itens) == set(dim_item)
    assert "3.4" not in itens and "2.1" not in itens


def test_ler_data_referencia_so_primeira_linha(bytes_estados):
    assert parse.ler_data_referencia(bytes_estados[:60]) == date(2026, 10, 30)


@pytest.mark.parametrize("nome,regra", [
    ("coluna_extra_5_9", "CAB_ITENS_CONHECIDOS"),
    ("sem_3_7", "CAB_ITENS_AUSENTES"),
    ("linha_curta", "LIN_LARGURA"),
    ("ibge_duplicado", "LIN_IBGE_UNICO"),
    ("so_cabecalho", "VOL_QTD_ENTES"),
])
def test_variantes_quebradas_rejeitadas_pela_regra_certa(bytes_estados, dim_item, nome, regra):
    falhas = bloqueios(variante(bytes_estados, nome), "ESTADO", dim_item)
    assert regra in falhas
    assert falhas[regra]  # mensagem legível


def test_item_novo_cita_descricao_da_legenda(bytes_estados, dim_item):
    conteudo = variante(bytes_estados, "coluna_extra_5_9").replace(
        b'"5.8";"Regularidade', b'"5.9";"Item de teste"\n"5.8";"Regularidade')
    msg = bloqueios(conteudo, "ESTADO", dim_item)["CAB_ITENS_CONHECIDOS"]
    assert "5.9 (Item de teste)" in msg and "aprovar-item" in msg


def test_utf8_detectado_sem_quebrar_acentos(bytes_estados, dim_item):
    arq = parse.ler(variante(bytes_estados, "utf8"))
    assert arq.encoding == "utf-8"
    assert "Amapá" in {c[1] for _, c in arq.linhas}
    res = {r.regra: r for r in validate.validar_estrutura(arq, "ESTADO", dim_item)}
    assert not res["META_ENCODING"].passou and res["META_ENCODING"].severidade == validate.AVISO
    assert validate.falhas(list(res.values()), validate.BLOQUEANTE) == []


def test_sem_legenda_e_aviso(bytes_estados, dim_item):
    arq = parse.ler(variante(bytes_estados, "sem_legenda"))
    assert len(arq.linhas) == 27
    res = validate.validar_estrutura(arq, "ESTADO", dim_item)
    assert [r.regra for r in validate.falhas(res)] == ["LEGENDA_AUSENTE"]


def test_tipo_ente_incoerente_com_recurso(bytes_estados, dim_item):
    assert "META_TIPO_ENTE" in bloqueios(bytes_estados, "MUNICIPIO", dim_item)


def test_descricao_mudou_e_aviso(bytes_estados, dim_item):
    dim_item["4.2"]["descricao"] = "texto antigo"
    res = {r.regra: r for r in validate.validar_estrutura(parse.ler(bytes_estados), "ESTADO", dim_item)}
    assert not res["LEG_DESCRICAO_MUDOU"].passou
    assert res["LEG_DESCRICAO_MUDOU"].detalhe["mudancas"] == {"4.2": "Regularidade Previdenciária"}


def test_marcador_legenda_sem_acento(bytes_estados):
    arq = parse.ler(bytes_estados.replace("Código do Itens".encode("latin-1"), b"Codigo do Itens"))
    assert len(arq.linhas) == 27 and len(arq.legenda) == 39
