import csv
import io
from pathlib import Path

import pytest

from cauc_ingest import config

FIX = Path(__file__).parent / "fixtures"
MUNICIPIOS = FIX / "municipios_20261030.csv"
ESTADOS = FIX / "estados_20261030.csv"


@pytest.fixture(scope="session")
def bytes_municipios() -> bytes:
    return MUNICIPIOS.read_bytes()


@pytest.fixture(scope="session")
def bytes_estados() -> bytes:
    return ESTADOS.read_bytes()


@pytest.fixture
def dim_item():
    return config.itens_seed()


def linhas_csv(conteudo: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(conteudo.decode("latin-1"), newline=""), delimiter=";"))


def montar_csv(linhas: list[list[str]], encoding: str = "latin-1") -> bytes:
    buf = io.StringIO()
    csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\n").writerows(linhas)
    return buf.getvalue().encode(encoding)


def fim_dados(linhas) -> int:
    return next(i for i, l in enumerate(linhas) if l and l[0] == "Código do Itens")


# --- variantes quebradas (prompt 04), derivadas do arquivo real de estados -------------------------

def variante(conteudo: bytes, nome: str) -> bytes:
    linhas = linhas_csv(conteudo)
    fim = fim_dados(linhas)
    if nome == "utf8":
        return conteudo.decode("latin-1").encode("utf-8")
    if nome == "coluna_extra_5_9":
        linhas[3].append("5.9")
        for l in linhas[4:fim]:
            l.append("30/10/26")
    elif nome == "sem_3_7":
        i = linhas[3].index("3.7")
        for l in linhas[3:fim]:
            del l[i]
    elif nome == "linha_curta":
        linhas[4].pop()
    elif nome == "ibge_duplicado":
        linhas[5][2] = linhas[4][2]
    elif nome == "sem_legenda":
        linhas = linhas[:fim]
    elif nome == "so_cabecalho":
        linhas = linhas[:4]
    else:
        raise ValueError(nome)
    return montar_csv(linhas)
