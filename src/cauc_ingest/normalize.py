"""Etapa 05: bronze (texto) -> silver (ente e fato longo ente × item, tipados)."""

import re
from dataclasses import dataclass
from datetime import date

from .parse import ArquivoCauc, sem_acento
from .validate import colunas_itens

RE_DATA_CURTA = re.compile(r"^(\d{2})/(\d{2})/(\d{2})$")
STATUS = ("REGULAR", "PENDENTE", "NAO_AVALIADO", "DESCONHECIDO")


@dataclass(frozen=True)
class Fato:
    codigo_ibge: str
    codigo_item: str
    valor_bruto: str
    status: str
    data_validade: date | None
    dias_para_vencer: int | None


def classificar_celula(valor: str, data_referencia: date) -> tuple[str, date | None, int | None]:
    """(status, data_validade, dias_para_vencer). Ano de 2 dígitos sempre 20aa (sem pivô da biblioteca)."""
    v = valor.strip()
    m = RE_DATA_CURTA.match(v)
    if m:
        try:
            validade = date(2000 + int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return "DESCONHECIDO", None, None
        return "REGULAR", validade, (validade - data_referencia).days
    if v == "!":
        return "PENDENTE", None, None
    if v.lower() == "desabilitado":
        return "NAO_AVALIADO", None, None
    return "DESCONHECIDO", None, None


def normalizar_nome(nome: str) -> str:
    return " ".join(sem_acento(nome).upper().split())


def _populacao(valor: str) -> int | None:
    v = valor.strip()
    if v == "" or v.lower() == "null":
        return None
    try:
        return int(v)
    except ValueError:
        return None


def normalizar(arq: ArquivoCauc) -> tuple[list[dict], list[Fato]]:
    """Só roda depois da validação estrutural (cabeçalho e larguras já conferidos)."""
    digitos = 7 if arq.tipo_ente == "MUNICIPIO" else 2
    itens = colunas_itens(arq.cabecalho)
    entes, fatos = [], []
    for _, cel in arq.linhas:
        ibge = cel[2].strip().zfill(digitos)
        nome = " ".join(cel[1].split())
        entes.append({
            "codigo_ibge": ibge,
            "codigo_siafi": cel[3].strip(),
            "uf": cel[0].strip().upper(),
            "nome": nome,
            "nome_normalizado": normalizar_nome(nome),
            "regiao": cel[4].strip().upper(),
            "populacao": _populacao(cel[5]),
            "fonte_populacao": cel[6].strip() or None,
        })
        for codigo, valor in zip(itens, cel[7:]):
            status, validade, dias = classificar_celula(valor, arq.data_referencia)
            fatos.append(Fato(ibge, codigo, valor.strip(), status, validade, dias))
    return entes, fatos
