"""Etapa 03: descoberta via CKAN, download seguro e landing zone imutável (raw)."""

import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from .config import CKAN_API, CKAN_DATASET, RECURSOS, TAMANHO_MINIMO, URL_FIXA, USER_AGENT, Config
from .parse import ler_data_referencia

log = logging.getLogger(__name__)
TENTATIVAS = 5


class ErroColeta(Exception):
    """Falha definitiva da coleta (vira alerta FALHA_DOWNLOAD ou MUDANCA_DE_LAYOUT)."""

    def __init__(self, mensagem: str, tipo_alerta: str = "FALHA_DOWNLOAD"):
        super().__init__(mensagem)
        self.tipo_alerta = tipo_alerta


class _Transitorio(Exception):
    pass


@dataclass
class ResultadoColeta:
    caminho: Path
    sha256: str
    data_referencia: date
    tamanho_bytes: int
    nova_versao: bool
    manifesto: dict


def novo_cliente(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Proxy corporativo via HTTP(S)_PROXY (trust_env)."""
    return httpx.Client(timeout=httpx.Timeout(300, connect=30), headers={"User-Agent": USER_AGENT},
                        follow_redirects=True, transport=transport, trust_env=True)


def _com_retentativas(funcao, dormir: Callable[[float], None]):
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            return funcao()
        except (_Transitorio, httpx.TimeoutException, httpx.TransportError) as erro:
            if tentativa == TENTATIVAS:
                raise ErroColeta(f"Esgotadas {TENTATIVAS} tentativas: {erro}") from erro
            espera = 2 ** tentativa + random.uniform(0, 1)
            log.warning("tentativa %s falhou (%s); nova tentativa em %.1fs", tentativa, erro, espera)
            dormir(espera)


def _get_json(cliente: httpx.Client, url: str, params: dict, dormir) -> dict:
    def chamar():
        r = cliente.get(url, params=params)
        if r.status_code == 429 or r.status_code >= 500:
            raise _Transitorio(f"HTTP {r.status_code} em {url}")
        r.raise_for_status()
        return r.json()
    return _com_retentativas(chamar, dormir)


def descobrir(cliente: httpx.Client, tipo: str, dormir=time.sleep) -> dict:
    """URL atual do recurso: resource_show -> package_show -> URL fixa (só municípios)."""
    resource_id, trecho = RECURSOS[tipo]
    recurso = None
    try:
        dados = _get_json(cliente, f"{CKAN_API}/resource_show", {"id": resource_id}, dormir)
        recurso = dados.get("result") if dados.get("success") else None
    except (ErroColeta, httpx.HTTPError, ValueError) as erro:
        log.warning("resource_show falhou para %s: %s; tentando package_show", tipo, erro)
    if recurso is None:
        try:
            pacote = _get_json(cliente, f"{CKAN_API}/package_show", {"id": CKAN_DATASET}, dormir)["result"]
            candidatos = [r for r in pacote["resources"] if r["id"] == resource_id] or \
                [r for r in pacote["resources"] if trecho in r.get("url", "")]
            recurso = candidatos[0] if len(candidatos) == 1 else None
            if recurso is None:
                raise ErroColeta(f"Recurso {resource_id} ({trecho}) não encontrado no dataset {CKAN_DATASET}",
                                 "MUDANCA_DE_LAYOUT")
        except ErroColeta:
            raise
        except (httpx.HTTPError, ValueError, KeyError) as erro:
            if tipo not in URL_FIXA:
                raise ErroColeta(f"API CKAN indisponível para {tipo}: {erro}") from erro
            log.warning("CKAN indisponível; usando URL fixa de %s", tipo)
            return {"url": URL_FIXA[tipo], "last_modified": None, "size": None, "format": "CSV", "fonte": "url_fixa"}
    if recurso.get("state", "active") != "active" or str(recurso.get("format", "")).upper() != "CSV":
        raise ErroColeta(f"Recurso {resource_id} mudou: state={recurso.get('state')} format={recurso.get('format')}",
                         "MUDANCA_DE_LAYOUT")
    return {"url": recurso["url"], "last_modified": recurso.get("last_modified"), "size": recurso.get("size"),
            "format": recurso.get("format"), "fonte": "ckan"}


def baixar(cliente: httpx.Client, url: str, destino_tmp: Path, tipo: str, dormir=time.sleep) -> tuple[str, int]:
    """Streaming para arquivo temporário; devolve (sha256, bytes). Apaga o temporário em qualquer falha."""
    destino_tmp.parent.mkdir(parents=True, exist_ok=True)

    def tentar():
        sha, total = hashlib.sha256(), 0
        try:
            with cliente.stream("GET", url) as r:
                if r.status_code == 429 or r.status_code >= 500:
                    raise _Transitorio(f"HTTP {r.status_code}")
                if r.status_code >= 400:
                    raise ErroColeta(f"HTTP {r.status_code} ao baixar {url}")
                if "html" in r.headers.get("content-type", "").lower():
                    raise ErroColeta(f"Portal devolveu HTML em vez de CSV ({url})")
                esperado = r.headers.get("content-length")
                with open(destino_tmp, "wb") as f:
                    for bloco in r.iter_bytes(1 << 16):
                        sha.update(bloco)
                        total += len(bloco)
                        f.write(bloco)
            if esperado is not None and int(esperado) != total:
                raise _Transitorio(f"download truncado: {total} de {esperado} bytes")
        except BaseException:
            destino_tmp.unlink(missing_ok=True)
            raise
        return sha.hexdigest(), total

    sha, total = _com_retentativas(tentar, dormir)
    with open(destino_tmp, "rb") as f:
        inicio = f.read(512)
    if total < TAMANHO_MINIMO[tipo] or inicio.lstrip().lower().startswith((b"<!doctype", b"<html")):
        destino_tmp.unlink(missing_ok=True)
        raise ErroColeta(f"Arquivo de {tipo} inválido: {total} bytes (mínimo {TAMANHO_MINIMO[tipo]}) ou conteúdo HTML")
    return sha, total


def guardar_raw(cfg: Config, tipo: str, tmp: Path, sha: str, tamanho: int, manifesto: dict) -> tuple[Path, date]:
    """Write-once: raw/tipo_ente=X/data_referencia=AAAA-MM-DD/<sha256>.csv (+ .manifest.json)."""
    with open(tmp, "rb") as f:
        data_ref = ler_data_referencia(f.readline())
    if data_ref is None:
        tmp.unlink(missing_ok=True)
        raise ErroColeta("Linha 1 sem 'Data da Pesquisa: dd/mm/aaaa'", "MUDANCA_DE_LAYOUT")
    pasta = cfg.pasta_raw / f"tipo_ente={tipo}" / f"data_referencia={data_ref.isoformat()}"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"{sha}.csv"
    if destino.exists():
        tmp.unlink()
    else:
        os.replace(tmp, destino)
        (pasta / f"{sha}.manifest.json").write_text(
            json.dumps({**manifesto, "sha256": sha, "tamanho_bytes": tamanho, "data_referencia": data_ref.isoformat(),
                        "tipo_ente": tipo}, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino, data_ref


def coletar(cfg: Config, tipo: str, cliente: httpx.Client, ja_existe: Callable[[date, str], bool],
            dormir=time.sleep) -> ResultadoColeta:
    """`ja_existe(data_referencia, sha256)` consulta cauc_carga_arquivo (idempotência)."""
    recurso = descobrir(cliente, tipo, dormir)
    manifesto = {"url_origem": recurso["url"], "ckan_last_modified": recurso["last_modified"],
                 "ckan_size": recurso["size"], "ckan_format": recurso["format"], "descoberta": recurso["fonte"],
                 "baixado_em": datetime.now(timezone.utc).isoformat()}
    tmp = cfg.pasta_tmp / f"{tipo}_{os.getpid()}_{time.time_ns()}.parcial"
    sha, tamanho = baixar(cliente, recurso["url"], tmp, tipo, dormir)
    if recurso["size"] and str(recurso["size"]) != str(tamanho):
        log.warning("tamanho informado pelo CKAN (%s) difere do baixado (%s)", recurso["size"], tamanho)
    caminho, data_ref = guardar_raw(cfg, tipo, tmp, sha, tamanho, manifesto)
    return ResultadoColeta(caminho, sha, data_ref, tamanho, not ja_existe(data_ref, sha), manifesto)
