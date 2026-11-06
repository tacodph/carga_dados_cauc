from cauc_ingest.match import EnteCauc, Entidade, casar, cnpj_valido, nome_chave

ENTES = [
    EnteCauc("MUNICIPIO", "3162500", "5131", "MG", "SAO JOAO DEL REI"),
    EnteCauc("MUNICIPIO", "1200013", "0643", "AC", "ACRELANDIA"),
    EnteCauc("MUNICIPIO", "2202307", "1083", "PI", "BOM JESUS"),
    EnteCauc("MUNICIPIO", "4302402", "8579", "RS", "BOM JESUS"),
    EnteCauc("MUNICIPIO", "3550308", "7107", "SP", "SAO PAULO"),
    EnteCauc("ESTADO", "35", "SP", "SP", "SAO PAULO"),
]
REF = {"46395000000139": ("MUNICIPIO", "3550308")}  # CNPJ da prefeitura de São Paulo


def por_id(entidades):
    return {m.id_entidade: m for m in casar(entidades, ENTES, REF)}


def test_cenarios_do_prompt_07():
    m = por_id([
        Entidade("ibge", ibge="1200013"),
        Entidade("siafi", siafi="0643"),
        Entidade("46395000000139"),
        Entidade("camara", uf="SP", nome_municipio="SÃO PAULO", ibge="355030", natureza="MUNICIPAL"),
        Entidade("homonimo_rs", uf="RS", nome_municipio="Bom Jesus", natureza="MUNICIPAL"),
        Entidade("homonimo_pi", uf="PI", nome_municipio="BOM JESUS", natureza="MUNICIPAL"),
        Entidade("grafia", uf="MG", nome_municipio="Sao Joao del-Rei", natureza="MUNICIPAL"),
        Entidade("secretaria_estadual", uf="SP", natureza="ESTADUAL"),
        Entidade("inexistente", uf="SP", nome_municipio="Cidade Que Nao Existe", natureza="MUNICIPAL"),
        Entidade("osc", uf="SP", nome_municipio="SÃO PAULO", ibge="355030", natureza=None),
    ])
    assert (m["ibge"].codigo_ibge, m["ibge"].metodo) == ("1200013", "IBGE")
    assert (m["siafi"].codigo_ibge, m["siafi"].metodo) == ("1200013", "SIAFI")
    assert (m["46395000000139"].metodo, m["46395000000139"].automatico) == ("CNPJ_VIA_TABELA", True)
    assert (m["camara"].codigo_ibge, m["camara"].metodo) == ("3550308", "ORGAO_VIA_IBGE6")
    assert m["homonimo_rs"].codigo_ibge == "4302402" and m["homonimo_pi"].codigo_ibge == "2202307"
    assert m["homonimo_rs"].metodo == "NOME_UF" and not m["homonimo_rs"].automatico
    assert m["grafia"].codigo_ibge == "3162500"
    assert (m["secretaria_estadual"].tipo_ente, m["secretaria_estadual"].codigo_ibge) == ("ESTADO", "35")
    assert "inexistente" not in m and "osc" not in m


def test_fuzzy_so_sugere_e_exige_mesma_uf():
    m = por_id([Entidade("f", uf="AC", nome_municipio="Acrelandiaa", natureza="MUNICIPAL"),
                Entidade("uf_errada", uf="RO", nome_municipio="Acrelandia", natureza="MUNICIPAL")])
    assert m["f"].metodo == "NOME_UF_FUZZY" and m["f"].confianca == 0.70 and not m["f"].automatico
    assert "uf_errada" not in m


def test_nome_chave_tira_prefixos():
    assert nome_chave("Prefeitura Municipal de São João del-Rei") == "SAO JOAO DEL REI"
    assert nome_chave("MUNICIPIO DE ACRELANDIA") == "ACRELANDIA"


def test_cnpj_valido():
    assert cnpj_valido("46395000000139") and cnpj_valido("17947649000117")
    assert not cnpj_valido("46395000000138") and not cnpj_valido("11111111111111") and not cnpj_valido("123")
