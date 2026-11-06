from datetime import date

import httpx
import pytest

from cauc_ingest import collect
from cauc_ingest.config import Config

URL_CSV = "https://exemplo.test/estados.csv"


def cfg(tmp_path) -> Config:
    return Config("h", 1, "db", "u", "s", pasta_dados=tmp_path)


def servidor(conteudo: bytes, falhas_csv: int = 0, tipo_conteudo="text/csv", truncar=False):
    estado = {"csv": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if "resource_show" in str(req.url):
            return httpx.Response(200, json={"success": True, "result": {
                "url": URL_CSV, "last_modified": "2026-10-30T11:11:09", "size": str(len(conteudo)), "format": "CSV",
                "state": "active"}})
        estado["csv"] += 1
        if estado["csv"] <= falhas_csv:
            return httpx.Response(503)
        cab = {"content-type": tipo_conteudo}
        if truncar:
            cab["content-length"] = str(len(conteudo) + 100)
        return httpx.Response(200, content=conteudo, headers=cab)
    return httpx.MockTransport(handler), estado


def rodar(tmp_path, transport, ja_existe=lambda d, s: False):
    return collect.coletar(cfg(tmp_path), "ESTADO", collect.novo_cliente(transport), ja_existe, dormir=lambda s: None)


def arquivos_raw(tmp_path):
    return sorted(p.name for p in (tmp_path / "raw").rglob("*") if p.is_file()) if (tmp_path / "raw").exists() else []


def test_sucesso_grava_raw_e_manifesto(tmp_path, bytes_estados):
    t, _ = servidor(bytes_estados)
    r = rodar(tmp_path, t)
    assert r.data_referencia == date(2026, 10, 30) and r.nova_versao
    assert r.caminho.read_bytes() == bytes_estados
    assert r.caminho.parent.name == "data_referencia=2026-10-30" and r.caminho.parent.parent.name == "tipo_ente=ESTADO"
    assert arquivos_raw(tmp_path) == sorted([f"{r.sha256}.csv", f"{r.sha256}.manifest.json"])
    assert not list((tmp_path / "tmp").iterdir())


def test_503_seguido_de_sucesso(tmp_path, bytes_estados):
    t, estado = servidor(bytes_estados, falhas_csv=2)
    assert rodar(tmp_path, t).nova_versao and estado["csv"] == 3


def test_html_no_lugar_do_csv(tmp_path):
    t, _ = servidor(b"<!DOCTYPE html><html>erro</html>" * 500, tipo_conteudo="text/html")
    with pytest.raises(collect.ErroColeta, match="HTML"):
        rodar(tmp_path, t)
    assert arquivos_raw(tmp_path) == []


def test_truncado_nao_escreve_no_raw(tmp_path, bytes_estados):
    t, estado = servidor(bytes_estados, truncar=True)
    with pytest.raises(collect.ErroColeta, match="truncado"):
        rodar(tmp_path, t)
    assert estado["csv"] == collect.TENTATIVAS
    assert arquivos_raw(tmp_path) == [] and not list((tmp_path / "tmp").iterdir())


def test_pequeno_demais(tmp_path):
    t, _ = servidor(b'"Data da Pesquisa: 30/10/2026"\n')
    with pytest.raises(collect.ErroColeta, match="inválido"):
        rodar(tmp_path, t)


def test_mesmo_hash_nao_e_nova_versao(tmp_path, bytes_estados):
    t, _ = servidor(bytes_estados)
    primeira = rodar(tmp_path, t)
    vistos = {(primeira.data_referencia, primeira.sha256)}
    segunda = rodar(tmp_path, t, lambda d, s: (d, s) in vistos)
    assert not segunda.nova_versao and segunda.caminho == primeira.caminho
    assert len(arquivos_raw(tmp_path)) == 2


def test_hash_diferente_mesma_data_e_nova_versao(tmp_path, bytes_estados):
    t, _ = servidor(bytes_estados)
    primeira = rodar(tmp_path, t)
    vistos = {(primeira.data_referencia, primeira.sha256)}
    t2, _ = servidor(bytes_estados.replace(b'"AC";"Acre"', b'"AC";"Acre "'))
    segunda = rodar(tmp_path, t2, lambda d, s: (d, s) in vistos)
    assert segunda.nova_versao and segunda.sha256 != primeira.sha256
    assert segunda.data_referencia == primeira.data_referencia
    assert len(arquivos_raw(tmp_path)) == 4


def test_formato_diferente_de_csv_falha(tmp_path):
    def handler(req):
        return httpx.Response(200, json={"success": True, "result": {"url": URL_CSV, "format": "XLSX", "state": "active"}})
    with pytest.raises(collect.ErroColeta) as erro:
        rodar(tmp_path, httpx.MockTransport(handler))
    assert erro.value.tipo_alerta == "MUDANCA_DE_LAYOUT"


def test_fallback_package_show(tmp_path, bytes_estados):
    def handler(req):
        if "resource_show" in str(req.url):
            return httpx.Response(404)
        if "package_show" in str(req.url):
            return httpx.Response(200, json={"success": True, "result": {"resources": [
                {"id": "83d2fbd7-4369-4935-85c2-366925cc5841", "url": URL_CSV, "format": "CSV", "state": "active"}]}})
        return httpx.Response(200, content=bytes_estados, headers={"content-type": "text/csv"})
    assert rodar(tmp_path, httpx.MockTransport(handler)).data_referencia == date(2026, 10, 30)
