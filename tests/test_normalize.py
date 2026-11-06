from collections import Counter
from datetime import date

import pytest

from cauc_ingest import normalize, parse

REF = date(2026, 10, 30)


@pytest.mark.parametrize("valor,status,validade", [
    ("31/12/99", "REGULAR", date(2099, 12, 31)),
    ("29/02/28", "REGULAR", date(2028, 2, 29)),
    ("29/02/27", "DESCONHECIDO", None),
    ("00/01/27", "DESCONHECIDO", None),
    ("!", "PENDENTE", None),
    ("Desabilitado", "NAO_AVALIADO", None),
    ("desabilitado ", "NAO_AVALIADO", None),
    ("X", "DESCONHECIDO", None),
    ("", "DESCONHECIDO", None),
])
def test_classificar_celula(valor, status, validade):
    s, v, _ = normalize.classificar_celula(valor, REF)
    assert (s, v) == (status, validade)


def test_dias_para_vencer():
    assert normalize.classificar_celula("30/01/27", REF)[2] == 92
    assert normalize.classificar_celula("30/10/26", REF)[2] == 0


@pytest.mark.parametrize("fixture,registros,regular,pendente,nao_avaliado,entes_pend", [
    ("bytes_municipios", 155_932, 139_818, 10_545, 5_569, 3_331),
    ("bytes_estados", 810, 759, 24, 27, 14),
])
def test_numeros_de_referencia(request, fixture, registros, regular, pendente, nao_avaliado, entes_pend):
    entes, fatos = normalize.normalizar(parse.ler(request.getfixturevalue(fixture)))
    c = Counter(f.status for f in fatos)
    assert len(fatos) == registros
    assert (c["REGULAR"], c["PENDENTE"], c["NAO_AVALIADO"], c["DESCONHECIDO"]) == (regular, pendente, nao_avaliado, 0)
    assert len({f.codigo_ibge for f in fatos if f.status == "PENDENTE"}) == entes_pend
    assert {f.codigo_item for f in fatos if f.status == "NAO_AVALIADO"} == {"1.3"}


def test_item_com_mais_pendencias(bytes_municipios, bytes_estados):
    for conteudo, esperado in ((bytes_municipios, ("3.2.3", 1740)), (bytes_estados, ("1.5", 9))):
        _, fatos = normalize.normalizar(parse.ler(conteudo))
        assert Counter(f.codigo_item for f in fatos if f.status == "PENDENTE").most_common(1)[0] == esperado


def test_df_e_siafi_com_zero(bytes_municipios, bytes_estados):
    est = {e["codigo_ibge"]: e for e in normalize.normalizar(parse.ler(bytes_estados))[0]}
    mun = {e["codigo_ibge"]: e for e in normalize.normalizar(parse.ler(bytes_municipios))[0]}
    assert est["53"]["populacao"] is None
    assert mun["1200013"]["codigo_siafi"] == "0643"
    assert mun["1200013"]["nome"] == "Acrelândia" and mun["1200013"]["nome_normalizado"] == "ACRELANDIA"


def test_municipio_nao_gera_itens_so_de_estado(bytes_municipios):
    _, fatos = normalize.normalizar(parse.ler(bytes_municipios))
    assert not {"4.3", "5.8"} & {f.codigo_item for f in fatos}
