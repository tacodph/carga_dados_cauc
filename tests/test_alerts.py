from datetime import date

from cauc_ingest.alerts import Alerta, ContextoNegocio, compor_mensagem, gerar_alertas_negocio

REF = date(2026, 11, 6)
CHAVE = ("MUNICIPIO", "3550308")


def ctx(**kw):
    base = dict(data_referencia=REF, eventos=[], apto_atual={CHAVE: False}, apto_anterior={CHAVE: True},
                pendentes_atuais={CHAVE: ["4.2"]}, persistentes=[], entidades={CHAVE: [("46395000000139", "PREFEITURA SP")]},
                itens_criticos={"4.2", "1.1"}, tipo_ente="MUNICIPIO")
    base.update(kw)
    return ContextoNegocio(**base)


def test_primeira_carga_so_resumo():
    a = gerar_alertas_negocio(ctx(primeira_carga=True, apto_anterior={}))
    assert [x.tipo for x in a] == ["RESUMO_LINHA_DE_BASE"]
    assert a[0].chave_dedup == "RESUMO_LINHA_DE_BASE|-|-|MUNICIPIO|2026-11-06"


def test_perdeu_aptidao_e_nova_pendencia():
    ev = [{"tipo_ente": "MUNICIPIO", "codigo_ibge": "3550308", "codigo_item": "4.2", "tipo_evento": "NOVA_PENDENCIA"},
          {"tipo_ente": "MUNICIPIO", "codigo_ibge": "3550308", "codigo_item": "3.3", "tipo_evento": "NOVA_PENDENCIA"},
          {"tipo_ente": "MUNICIPIO", "codigo_ibge": "1", "codigo_item": "4.2", "tipo_evento": "NOVA_PENDENCIA"}]
    a = {(x.tipo, x.codigo_item, x.severidade) for x in gerar_alertas_negocio(ctx(eventos=ev))}
    assert a == {("NOVA_PENDENCIA", "4.2", "ALTA"), ("PERDEU_APTIDAO", None, "CRITICA")}  # 3.3 não é crítico; ente 1 sem entidade


def test_dedup_estavel_entre_reprocessos():
    a1 = gerar_alertas_negocio(ctx())
    a2 = gerar_alertas_negocio(ctx())
    assert [x.chave_dedup for x in a1] == [x.chave_dedup for x in a2] == ["PERDEU_APTIDAO|46395000000139|-|2026-11-06"]


def test_supressao_por_item_instavel():
    ev = [{"tipo_ente": "MUNICIPIO", "codigo_ibge": "3550308", "codigo_item": "4.2", "tipo_evento": "NOVA_PENDENCIA"}]
    assert gerar_alertas_negocio(ctx(eventos=ev, itens_suprimidos={"4.2"})) == []


def test_silencio():
    assert gerar_alertas_negocio(ctx(silencios=[("46395000000139", None, None)])) == []
    assert gerar_alertas_negocio(ctx(silencios=[(None, None, "PERDEU_APTIDAO")])) == []


def test_pendencia_persistente_repete_a_cada_4_semanas():
    p = [("MUNICIPIO", "3550308", "4.2", date(2026, 10, 2))]  # 5 semanas
    a = [x for x in gerar_alertas_negocio(ctx(persistentes=p, apto_anterior={CHAVE: False})) if x.tipo == "PENDENCIA_PERSISTENTE"]
    assert a[0].chave_dedup.endswith("desde=2026-10-02|bloco=1")
    p3 = [("MUNICIPIO", "3550308", "4.2", date(2026, 10, 23))]  # 2 semanas
    assert not [x for x in gerar_alertas_negocio(ctx(persistentes=p3)) if x.tipo == "PENDENCIA_PERSISTENTE"]


def test_mensagem_traz_data_ref_descricao_e_aviso():
    a = gerar_alertas_negocio(ctx()) + [Alerta("VERSAO_REJEITADA", "ALTA", REF, payload={"motivo": "x"})]
    texto = compor_mensagem(a, REF, {"4.2": "Regularidade Previdenciária"}, {"MUNICIPIO": "PUBLICADO"})
    assert "06/11/2026 (semana 2026-W45)" in texto
    assert "CRITICA · PERDEU_APTIDAO" in texto and "pendências atuais: 4.2" in texto
    assert "fonte oficial" in texto and "OPERACIONAL · ALTA · VERSAO_REJEITADA" in texto
