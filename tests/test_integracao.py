"""Pipeline ponta a ponta no banco transferepro (schema base_tesouro_nacional).

ATENÇÃO: trunca TODAS as tabelas cauc_* antes e depois de cada teste (nada fora de cauc_* é alterado; as tabelas do
sistema só são lidas). Só rode antes da primeira carga real ou quando puder recarregar: CAUC_TESTE_BANCO=1 pytest -m integracao
"""

import os
from datetime import date, datetime

import httpx
import pytest

from cauc_ingest import cli, config, db, history, pipeline
from cauc_ingest.db import q
from conftest import fim_dados, linhas_csv, montar_csv, variante

pytestmark = pytest.mark.integracao
SP_CNPJ = "46395000000139"
SP = "3550308"
ALVO, ALVO_CNPJ, OSC_CNPJ = "1506807", "05182233000176", "03376129000197"  # Santarém (monitorada) e uma OSC


# ------------------------------------------------------------------ infraestrutura

def limpar_cauc(conn) -> None:
    tabelas = [r[0] for r in conn.execute(r"""select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'base_tesouro_nacional' and c.relkind = 'r' and c.relname like 'cauc\_%'""")]
    assert tabelas and all(t.startswith("cauc_") for t in tabelas)
    conn.execute(q("truncate " + ", ".join(f"{{s}}.{t}" for t in tabelas) + " restart identity cascade"))


@pytest.fixture
def cfg(tmp_path):
    if os.environ.get("CAUC_TESTE_BANCO") != "1":
        pytest.skip("trunca as tabelas cauc_*: defina CAUC_TESTE_BANCO=1 para rodar")
    c = config.Config(**{**config.carregar().__dict__, "pasta_dados": tmp_path})
    with db.conectar(c) as conn:
        limpar_cauc(conn)
    yield c
    with db.conectar(c) as conn:
        limpar_cauc(conn)


class Servidor:
    """CKAN + download + Siconfi falsos; `arquivos[tipo]` é o que será servido."""

    def __init__(self, municipios: bytes, estados: bytes):
        self.arquivos = {"MUNICIPIO": municipios, "ESTADO": estados}

    def handler(self, req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "resource_show" in u:
            tipo = "MUNICIPIO" if req.url.params["id"] == config.RECURSOS["MUNICIPIO"][0] else "ESTADO"
            return httpx.Response(200, json={"success": True, "result": {
                "url": f"https://fake.test/{tipo}.csv", "format": "CSV", "state": "active", "size": None,
                "last_modified": None}})
        if "siconfi" in u:
            return httpx.Response(200, json={"items": [
                {"cod_ibge": 3550308, "ente": "São Paulo", "esfera": "M", "cnpj": SP_CNPJ},
                {"cod_ibge": 35, "ente": "São Paulo", "esfera": "E", "cnpj": "46379400000150"}]})
        tipo = "MUNICIPIO" if "MUNICIPIO" in u else "ESTADO"
        return httpx.Response(200, content=self.arquivos[tipo], headers={"content-type": "text/csv"})

    def cliente(self):
        from cauc_ingest.collect import novo_cliente
        return novo_cliente(httpx.MockTransport(self.handler))


def rodar(cfg, srv, agora=datetime(2026, 11, 9, 8, tzinfo=pipeline.TZ), forcar=True):
    return pipeline.executar(cfg, agora=agora, forcar=forcar, cliente=srv.cliente())


def um(conn, sql, *args):
    return conn.execute(q(sql), args).fetchone()[0]


def itens_novos(linha, cab) -> list[int]:
    return [i for i in range(7, len(linha)) if linha[i] not in ("!", "Desabilitado")][:5]


def semana2(conteudo: bytes, data="06/11/2026", resolver=10) -> bytes:
    """`resolver` pendências resolvidas, 5 novas em Santarém (entidade monitorada), Acrelândia ausente."""
    linhas = linhas_csv(conteudo)
    fim = fim_dados(linhas)
    linhas[0] = [f"Data da Pesquisa: {data}"]
    cab = linhas[3]
    feitas = 0
    for l in linhas[4:fim]:
        if l[2] == ALVO:
            for i in itens_novos(l, cab):
                l[i] = "!"
            continue
        for i in range(7, len(l)):
            if l[i] == "!" and feitas < resolver and l[2] != "1200013":
                l[i], feitas = "30/01/27", feitas + 1
    linhas = [l for l in linhas if not (len(l) > 2 and l[2] == "1200013")]
    return montar_csv(linhas)


ASSINATURA = {
    "cauc_fato_situacao_item": "data_referencia, tipo_ente, codigo_ibge, codigo_item, valor_bruto, status, data_validade",
    "cauc_fato_ente": "data_referencia, tipo_ente, codigo_ibge, codigo_siafi, nome, populacao",
    "cauc_hist_situacao_item": "*",
    "cauc_evento_mudanca": "*",
    "cauc_dim_ente": "tipo_ente, codigo_ibge, codigo_siafi, nome, populacao, valido_de, valido_ate, atual",
}


def assinatura(conn) -> dict:
    """md5 do conteúdo estável de cada tabela (sem chaves substitutas nem timestamps)."""
    return {t: um(conn, f"""select md5(coalesce(string_agg(x, '|' order by x), ''))
                            from (select {'t::text' if c == '*' else f'row({c})::text'} as x from {{s}}.{t} t) y""")
            for t, c in ASSINATURA.items()}


# ------------------------------------------------------------------ cenários

def test_tres_semanas_republicacao_e_idempotencia(cfg, bytes_municipios, bytes_estados):
    srv = Servidor(bytes_municipios, bytes_estados)
    assert rodar(cfg, srv) == 0
    with db.conectar(cfg) as c:
        assert um(c, "select count(*) from {s}.cauc_fato_situacao_item where tipo_ente = 'MUNICIPIO'") == 155_932
        assert um(c, "select count(*) from {s}.cauc_fato_situacao_item where tipo_ente = 'ESTADO'") == 810
        assert um(c, "select count(*) from {s}.cauc_vw_situacao_atual where qtd_pendentes > 0 and tipo_ente = 'MUNICIPIO'") == 3331
        assert um(c, "select count(*) from {s}.cauc_vw_situacao_atual where qtd_pendentes > 0 and tipo_ente = 'ESTADO'") == 14
        assert um(c, "select count(*) from {s}.cauc_evento_mudanca") == 0
        assert um(c, "select array_agg(distinct tipo) from {s}.cauc_alerta where id_entidade_sistema is not null") is None
        assert um(c, "select count(*) from {s}.cauc_alerta where tipo = 'RESUMO_LINHA_DE_BASE'") == 2
        assert um(c, "select codigo_siafi from {s}.cauc_dim_ente where codigo_ibge = '1200013'") == "0643"
        assert um(c, "select nome from {s}.cauc_dim_ente where codigo_ibge = '1200013'") == "Acrelândia"
        assert um(c, "select metodo_match from {s}.cauc_map_entidade where id_entidade_sistema = %s", SP_CNPJ) == "CNPJ_VIA_TABELA"
        assert um(c, "select metodo_match from {s}.cauc_map_entidade where id_entidade_sistema = %s", ALVO_CNPJ) == "ORGAO_VIA_IBGE6"
        assert um(c, "select count(*) from {s}.cauc_map_entidade where id_entidade_sistema = %s", OSC_CNPJ) == 0
        assert um(c, "select count(*) from {s}.cauc_vw_situacao_entidade_sistema where monitorada and codigo_ibge = %s", ALVO) >= 1
        cargas, alertas = um(c, "select count(*) from {s}.cauc_carga_arquivo"), um(c, "select count(*) from {s}.cauc_alerta")

    # segunda execução sem arquivo novo: nada novo
    assert rodar(cfg, srv) == 0
    with db.conectar(cfg) as c:
        assert um(c, "select count(*) from {s}.cauc_carga_arquivo") == cargas
        assert um(c, "select count(*) from {s}.cauc_alerta") == alertas

    # semana 2: 10 resolvidas, 5 novas, 1 ente ausente -> COM RESSALVA (DRIFT_ENTES_AUSENTES), publicada
    srv.arquivos["MUNICIPIO"] = semana2(bytes_municipios)
    linhas = linhas_csv(bytes_municipios)
    linha_alvo = next(l for l in linhas if len(l) > 2 and l[2] == ALVO)
    item_alvo = linhas[3][itens_novos(linha_alvo, linhas[3])[0]]
    assert rodar(cfg, srv, agora=datetime(2026, 11, 9, 8, tzinfo=pipeline.TZ)) == 0
    with db.conectar(cfg) as c:
        ev = dict(c.execute(q("""select tipo_evento, count(*) from {s}.cauc_vw_mudancas_semana
                                 where tipo_ente = 'MUNICIPIO' group by 1""")).fetchall())
        assert ev.get("PENDENCIA_RESOLVIDA") == 10 and ev.get("NOVA_PENDENCIA") == 5 and ev.get("ENTE_AUSENTE") == 1
        assert um(c, "select com_ressalva from {s}.cauc_carga_arquivo where data_referencia = '2026-11-06'") is True
        tipos = {r[0] for r in c.execute(q("select tipo from {s}.cauc_alerta where id_entidade_sistema = %s"), (ALVO_CNPJ,))}
        assert "NOVA_PENDENCIA" in tipos
        assert um(c, "select count(*) from {s}.cauc_alerta where tipo = 'MATCH_ENTE_AUSENTE' or payload->>'regra' = 'MATCH_ENTE_AUSENTE'") == 1
        assert um(c, """select valido_de from {s}.cauc_hist_situacao_item
                        where codigo_ibge = %s and codigo_item = %s and atual""", ALVO, item_alvo) == date(2026, 11, 6)
        assert um(c, """select status from {s}.cauc_fato_situacao_item where codigo_ibge = %s
                        and data_referencia = '2026-10-30' and codigo_item = %s""", ALVO, item_alvo) != "PENDENTE"
        antes = assinatura(c)
        alertas = um(c, "select count(*) from {s}.cauc_alerta")

    # reprocessar a semana 2 não muda nada nem reenvia alerta
    pipeline.reprocessar(cfg, "MUNICIPIO", date(2026, 11, 6))
    with db.conectar(cfg) as c:
        assert assinatura(c) == antes
        assert um(c, "select count(*) from {s}.cauc_alerta where id_entidade_sistema is not null") == \
            um(c, "select count(distinct chave_dedup) from {s}.cauc_alerta where id_entidade_sistema is not null")
        assert um(c, "select count(*) from {s}.cauc_alerta") == alertas

    # semana 3: republicação da semana 2 (mesma data, outro sha, 11 resolvidas)
    srv.arquivos["MUNICIPIO"] = semana2(bytes_municipios, resolver=11)
    assert rodar(cfg, srv) == 0
    with db.conectar(cfg) as c:
        st = dict(c.execute(q("""select status, count(*) from {s}.cauc_carga_arquivo
                                 where tipo_ente = 'MUNICIPIO' and data_referencia = '2026-11-06' group by 1""")).fetchall())
        assert st == {"PUBLICADO": 1, "SUBSTITUIDO": 1}
        assert um(c, """select count(*) from {s}.cauc_evento_mudanca where data_referencia = '2026-11-06'
                        and tipo_evento = 'PENDENCIA_RESOLVIDA'""") == 11
        assert um(c, "select count(*) from {s}.cauc_alerta where tipo = 'REPUBLICACAO'") == 1


def test_backfill_fora_de_ordem_igual_ao_incremental(cfg, tmp_path, bytes_municipios, bytes_estados):
    s2 = semana2(bytes_municipios)
    srv = Servidor(bytes_municipios, bytes_estados)
    rodar(cfg, srv)
    srv.arquivos["MUNICIPIO"] = s2
    rodar(cfg, srv)
    with db.conectar(cfg) as c:
        incremental = assinatura(c)
        for t in ("cauc_bronze_linha", "cauc_fato_situacao_item", "cauc_fato_ente", "cauc_hist_situacao_item",
                  "cauc_evento_mudanca", "cauc_dim_ente", "cauc_dq_metrica", "cauc_dq_resultado", "cauc_carga_arquivo"):
            c.execute(q(f"truncate {{s}}.{t} cascade"))
    for nome, conteudo in (("novo", [("m2.csv", s2)]), ("antigo", [("m1.csv", bytes_municipios), ("e1.csv", bytes_estados)])):
        pasta = tmp_path / f"backfill_{nome}"
        pasta.mkdir()
        for arquivo, b in conteudo:
            (pasta / arquivo).write_bytes(b)
        resultado = pipeline.backfill(cfg, pasta)
        assert set(resultado.values()) <= {"PUBLICADO", "COM_RESSALVA"}
    with db.conectar(cfg) as c:
        assert assinatura(c) == incremental


def test_versao_bloqueada_nao_altera_vista_e_aprovar_item_publica(cfg, bytes_municipios, bytes_estados):
    srv = Servidor(bytes_municipios, bytes_estados)
    rodar(cfg, srv)
    with db.conectar(cfg) as c:
        antes = c.execute(q("select * from {s}.cauc_vw_situacao_atual order by 1, 2, 3")).fetchall()
    novo = variante(bytes_estados, "coluna_extra_5_9").replace(b"30/10/2026", b"06/11/2026", 1)
    srv.arquivos["ESTADO"] = novo
    assert rodar(cfg, srv) == 3
    with db.conectar(cfg) as c:
        assert c.execute(q("select * from {s}.cauc_vw_situacao_atual order by 1, 2, 3")).fetchall() == antes
        assert um(c, "select status from {s}.cauc_carga_arquivo order by id_carga desc limit 1") == "REJEITADO"
        assert um(c, "select count(*) from {s}.cauc_alerta where tipo in ('MUDANCA_DE_LAYOUT', 'VERSAO_REJEITADA')") == 2
    assert cli.main(["aprovar-item", "5.9", "--descricao", "Item de teste", "--estado"]) == 0
    assert pipeline.reprocessar(cfg, "ESTADO", date(2026, 11, 6)) in ("PUBLICADO", "COM_RESSALVA")
    with db.conectar(cfg) as c:
        assert um(c, "select max(data_referencia) from {s}.cauc_vw_situacao_atual where tipo_ente = 'ESTADO'") == date(2026, 11, 6)
        assert um(c, "select count(*) from {s}.cauc_evento_mudanca where tipo_evento = 'ITEM_NOVO'") == 27


def test_concorrencia_e_falha_no_meio_da_publicacao(cfg, bytes_municipios, bytes_estados, monkeypatch):
    srv = Servidor(bytes_municipios, bytes_estados)
    with db.conectar(cfg) as outra:
        outra.execute("select pg_advisory_lock(hashtext('cauc:ESTADO'))")
        assert pipeline.executar(cfg, agora=datetime(2026, 11, 9, 8, tzinfo=pipeline.TZ), forcar=True,
                                 cliente=srv.cliente(), tipos=("ESTADO",)) == 2
    with db.conectar(cfg) as c:
        assert um(c, "select count(*) from {s}.cauc_carga_arquivo") == 0

    def explode(*a, **k):
        raise RuntimeError("processo morto no meio da publicação")
    monkeypatch.setattr(history, "reconstruir_eventos", explode)
    assert rodar(cfg, srv) == 3
    with db.conectar(cfg) as c:
        assert um(c, "select count(*) from {s}.cauc_fato_situacao_item") == 0
        assert um(c, "select count(*) from {s}.cauc_vw_situacao_atual") == 0
        assert um(c, "select count(*) from {s}.cauc_carga_arquivo where status = 'PUBLICADO'") == 0
    monkeypatch.undo()


def test_janela_sem_arquivo_novo_alerta_uma_vez(cfg, bytes_municipios, bytes_estados):
    srv = Servidor(bytes_municipios, bytes_estados)
    rodar(cfg, srv)
    terca = datetime(2030, 1, 8, 8, tzinfo=pipeline.TZ)  # futuro: publicado_em (relógio real) fica antes da janela
    assert rodar(cfg, srv, agora=terca, forcar=False) == 0
    assert rodar(cfg, srv, agora=terca, forcar=False) == 0
    with db.conectar(cfg) as c:
        assert um(c, "select count(*) from {s}.cauc_alerta where tipo = 'ARQUIVO_NAO_ATUALIZADO'") == 2  # 1 por tipo
    assert rodar(cfg, srv, agora=datetime(2030, 1, 9, 8, tzinfo=pipeline.TZ), forcar=False) == 0  # fora da janela
