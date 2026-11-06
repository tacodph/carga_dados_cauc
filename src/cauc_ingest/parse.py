"""Etapa 04: leitura do CSV em blocos (metadados, cabeçalho, dados, legenda). Não converte tipos."""

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from .config import TIPO_ENTE_ARQUIVO

RE_DATA_PESQUISA = re.compile(r"^Data da Pesquisa: (\d{2})/(\d{2})/(\d{4})$")
RE_TIPO_ENTE = re.compile(r"^Tipo de Ente: (.+)$")


@dataclass
class ArquivoCauc:
    meta: list[str]  # linhas 1..3 como vieram
    data_referencia: date | None
    tipo_ente: str | None  # MUNICIPIO | ESTADO | None (não reconhecido)
    abrangencia: str | None
    cabecalho: list[str]
    linhas: list[tuple[int, list[str]]]  # (nº da linha no arquivo, células)
    legenda: list[tuple[str, str]]
    encoding: str
    avisos: list[str] = field(default_factory=list)


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def decodificar(conteudo: bytes) -> tuple[str, str]:
    """latin-1 é o contrato; se o arquivo for UTF-8 válido com acentos, é UTF-8 (o Tesouro mudou o encoding).

    Bytes latin-1 com acento quase nunca formam UTF-8 válido, por isso tentar UTF-8 estrito primeiro é seguro.
    """
    if conteudo.startswith(b"\xef\xbb\xbf"):
        return conteudo[3:].decode("utf-8"), "utf-8-sig"
    try:
        texto = conteudo.decode("utf-8")
        if not texto.isascii():
            return texto, "utf-8"
    except UnicodeDecodeError:
        pass
    return conteudo.decode("latin-1"), "latin-1"


def data_pesquisa(linha: str) -> date | None:
    m = RE_DATA_PESQUISA.match(linha.strip())
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def ler_data_referencia(primeira_linha: bytes) -> date | None:
    """Só a 1ª linha (para nomear a landing zone antes de parsear)."""
    texto, _ = decodificar(primeira_linha.split(b"\n", 1)[0])
    return data_pesquisa(texto.strip().strip('"'))


def _eh_marcador_legenda(celula: str) -> bool:
    return sem_acento(celula).strip().lower() == "codigo do itens"


def ler(conteudo: bytes) -> ArquivoCauc:
    texto, encoding = decodificar(conteudo)
    linhas = list(csv.reader(io.StringIO(texto, newline=""), delimiter=";", quotechar='"'))
    meta = [(linhas[i][0].strip() if i < len(linhas) and linhas[i] else "") for i in range(3)]
    tipo = RE_TIPO_ENTE.match(meta[1])
    abrangencia = meta[2].split(":", 1)[1].strip() if ":" in meta[2] else None
    arq = ArquivoCauc(
        meta=meta,
        data_referencia=data_pesquisa(meta[0]),
        tipo_ente=TIPO_ENTE_ARQUIVO.get(tipo[1].strip()) if tipo else None,
        abrangencia=abrangencia,
        cabecalho=[c.strip() for c in linhas[3]] if len(linhas) > 3 else [],
        linhas=[],
        legenda=[],
        encoding=encoding,
    )
    na_legenda = False
    for num, celulas in enumerate(linhas[4:], start=5):
        if not any(c.strip() for c in celulas):
            continue
        if not na_legenda and _eh_marcador_legenda(celulas[0]):
            na_legenda = True
            continue
        if na_legenda:
            arq.legenda.append((celulas[0].strip(), celulas[1].strip() if len(celulas) > 1 else ""))
        else:
            arq.linhas.append((num, celulas))
    if not na_legenda:
        arq.avisos.append("LEGENDA_AUSENTE")
    return arq


def itens_da_legenda(legenda: list[tuple[str, str]]) -> dict[str, str]:
    """Só itens (folhas numéricas): descarta grupos romanos e subgrupos (código que é prefixo de outro)."""
    codigos = [c for c, _ in legenda if re.fullmatch(r"\d+(\.\d+)*", c)]
    subgrupos = {c for c in codigos if any(o.startswith(c + ".") for o in codigos)}
    return {c: d for c, d in legenda if c in codigos and c not in subgrupos}
