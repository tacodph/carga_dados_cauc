from collections import Counter
from datetime import date

from cauc_ingest import normalize, parse
from cauc_ingest.history import calcular_eventos


def snapshot(conteudo):
    _, fatos = normalize.normalizar(parse.ler(conteudo))
    return {(f.codigo_ibge, f.codigo_item): (f.status, f.data_validade) for f in fatos}


def semana2(s1):
    """10 pendências resolvidas, 5 novas, 1 ente ausente (cenário do prompt 06)."""
    s2 = dict(s1)
    pend = sorted(k for k, v in s1.items() if v[0] == "PENDENTE" and k[0] != "1200013")[:10]
    reg = sorted(k for k, v in s1.items() if v[0] == "REGULAR" and k[0] != "1200013")[:5]
    for k in pend:
        s2[k] = ("REGULAR", date(2027, 1, 30))
    for k in reg:
        s2[k] = ("PENDENTE", None)
    for k in [k for k in s2 if k[0] == "1200013"]:
        del s2[k]
    return s2


def test_primeira_carga_sem_eventos(bytes_municipios):
    assert calcular_eventos({}, snapshot(bytes_municipios)) == []


def test_eventos_batem_com_alteracoes_injetadas(bytes_municipios):
    s1 = snapshot(bytes_municipios)
    s2 = semana2(s1)
    c = Counter(e.tipo_evento for e in calcular_eventos(s1, s2))
    assert c == {"PENDENCIA_RESOLVIDA": 10, "NOVA_PENDENCIA": 5, "ENTE_AUSENTE": 1}
    c_volta = Counter(e.tipo_evento for e in calcular_eventos(s2, s1))
    assert c_volta == {"PENDENCIA_RESOLVIDA": 5, "NOVA_PENDENCIA": 10, "ENTE_NOVO": 1}


def test_renovacao_e_mudanca_de_avaliacao():
    a = {("1", "1.1"): ("REGULAR", date(2026, 11, 1)), ("1", "1.3"): ("NAO_AVALIADO", None), ("1", "1.2"): ("REGULAR", None)}
    b = {("1", "1.1"): ("REGULAR", date(2027, 1, 1)), ("1", "1.3"): ("REGULAR", date(2027, 1, 1)), ("1", "4.3"): ("PENDENTE", None)}
    tipos = {(e.codigo_item, e.tipo_evento) for e in calcular_eventos(a, b)}
    assert tipos == {("1.1", "RENOVADA"), ("1.3", "MUDANCA_AVALIACAO"), ("4.3", "ITEM_NOVO"), ("1.2", "ITEM_REMOVIDO")}


def test_nao_avaliado_para_pendente_e_nova_pendencia():
    ev = calcular_eventos({("1", "1.3"): ("NAO_AVALIADO", None)}, {("1", "1.3"): ("PENDENTE", None)})
    assert [e.tipo_evento for e in ev] == ["NOVA_PENDENCIA"]
