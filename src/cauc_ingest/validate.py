"""Etapa 04: validação estrutural (invariantes do contrato §8). Toda regra aqui é BLOQUEANTE, salvo indicação."""

import re
from collections import Counter
from dataclasses import dataclass, field

from .config import COLUNAS_FIXAS, FAIXA_ENTES
from .parse import ArquivoCauc, itens_da_legenda

BLOQUEANTE, QUARENTENA, AVISO = "BLOQUEANTE", "QUARENTENA", "AVISO"


@dataclass
class Resultado:
    regra: str
    severidade: str
    passou: bool
    mensagem: str = ""
    valor_observado: float | None = None
    limite: str | None = None
    detalhe: dict = field(default_factory=dict)


def falhas(resultados: list[Resultado], severidade: str | None = None) -> list[Resultado]:
    return [r for r in resultados if not r.passou and (severidade is None or r.severidade == severidade)]


def _r(regra, sev, ok, msg_falha, **kw) -> Resultado:
    return Resultado(regra, sev, ok, "" if ok else msg_falha, **kw)


def colunas_itens(cabecalho: list[str]) -> list[str]:
    return cabecalho[len(COLUNAS_FIXAS):]


def validar_estrutura(arq: ArquivoCauc, tipo_esperado: str, dim_item: dict[str, dict]) -> list[Resultado]:
    """`dim_item`: codigo_item -> {descricao, aplica_municipio, aplica_estado}."""
    res: list[Resultado] = []
    res.append(_r("META_DATA_PESQUISA", BLOQUEANTE, arq.data_referencia is not None,
                  f"Linha 1 não é 'Data da Pesquisa: dd/mm/aaaa' válida: {arq.meta[0]!r}"))
    res.append(_r("META_TIPO_ENTE", BLOQUEANTE, arq.tipo_ente == tipo_esperado,
                  f"Linha 2 {arq.meta[1]!r} não corresponde ao recurso baixado ({tipo_esperado})"))
    res.append(_r("META_ENCODING", AVISO, arq.encoding == "latin-1",
                  f"Encoding detectado {arq.encoding} (esperado latin-1): o Tesouro mudou o arquivo",
                  detalhe={"encoding": arq.encoding}))
    res.append(_r("LEGENDA_AUSENTE", AVISO, "LEGENDA_AUSENTE" not in arq.avisos,
                  "Linha 'Código do Itens' não encontrada: legenda ausente"))

    fixas = arq.cabecalho[: len(COLUNAS_FIXAS)]
    ok_fixas = fixas == COLUNAS_FIXAS
    res.append(_r("CAB_COLUNAS_FIXAS", BLOQUEANTE, ok_fixas,
                  f"Colunas A..G esperadas {COLUNAS_FIXAS}, encontradas {fixas}"))

    itens = colunas_itens(arq.cabecalho) if ok_fixas else []
    legenda = itens_da_legenda(arq.legenda)
    novos = [c for c in itens if c not in dim_item]
    res.append(_r("CAB_ITENS_CONHECIDOS", BLOQUEANTE, not novos,
                  "Item novo no CAUC: " + ", ".join(f"{c} ({legenda.get(c, 'sem descrição na legenda')})" for c in novos)
                  + ". Aprove com `cauc-ingest aprovar-item` e reprocesse.",
                  valor_observado=len(novos), detalhe={"itens_novos": {c: legenda.get(c) for c in novos}}))
    chave_aplica = "aplica_municipio" if tipo_esperado == "MUNICIPIO" else "aplica_estado"
    esperados = [c for c, i in dim_item.items() if i[chave_aplica]]
    ausentes = [c for c in esperados if c not in itens] if ok_fixas else []
    res.append(_r("CAB_ITENS_AUSENTES", BLOQUEANTE, not ausentes,
                  f"Item removido do CAUC (esperado para {tipo_esperado}): {', '.join(ausentes)}",
                  valor_observado=len(ausentes), detalhe={"itens_ausentes": ausentes}))
    duplicadas = [c for c, n in Counter(arq.cabecalho).items() if n > 1]
    if duplicadas:
        res.append(_r("CAB_COLUNAS_DUPLICADAS", BLOQUEANTE, False, f"Colunas repetidas no cabeçalho: {duplicadas}"))

    largas = [num for num, cel in arq.linhas if len(cel) != len(arq.cabecalho)]
    res.append(_r("LIN_LARGURA", BLOQUEANTE, not largas,
                  f"{len(largas)} linha(s) com nº de colunas diferente do cabeçalho ({len(arq.cabecalho)}): "
                  f"linhas {largas[:10]}", valor_observado=len(largas), detalhe={"exemplos": largas[:10]}))

    if ok_fixas:
        ibges = [cel[2].strip() for _, cel in arq.linhas if len(cel) > 3]
        siafis = [cel[3].strip() for _, cel in arq.linhas if len(cel) > 3]
        for regra, valores in (("LIN_IBGE_UNICO", ibges), ("LIN_SIAFI_UNICO", siafis)):
            dup = sorted(v for v, n in Counter(valores).items() if n > 1)
            res.append(_r(regra, BLOQUEANTE, not dup, f"Códigos repetidos: {dup[:10]}",
                          valor_observado=len(dup), detalhe={"exemplos": dup[:10]}))
        digitos = 7 if tipo_esperado == "MUNICIPIO" else 2
        ruins = [v for v in ibges if not re.fullmatch(rf"\d{{{digitos}}}", v)]
        res.append(_r("LIN_IBGE_FORMATO", BLOQUEANTE, not ruins,
                      f"Código IBGE fora do formato de {digitos} dígitos: {ruins[:10]}",
                      valor_observado=len(ruins), detalhe={"exemplos": ruins[:10]}))

    minimo, maximo = FAIXA_ENTES[tipo_esperado]
    qtd = len(arq.linhas)
    res.append(_r("VOL_QTD_ENTES", BLOQUEANTE, minimo <= qtd <= maximo,
                  f"{qtd} entes no arquivo; faixa esperada {minimo} a {maximo}",
                  valor_observado=qtd, limite=f"{minimo}..{maximo}"))

    mudou = {c: (dim_item[c]["descricao"], d) for c, d in legenda.items() if c in dim_item and dim_item[c]["descricao"] != d}
    res.append(_r("LEG_DESCRICAO_MUDOU", AVISO, not mudou,
                  "Descrição de item mudou na legenda: " + "; ".join(f"{c}: {a!r} → {n!r}" for c, (a, n) in mudou.items()),
                  detalhe={"mudancas": {c: n for c, (_, n) in mudou.items()}}))
    return res
