"""Parâmetros fixos do CAUC (contrato de dados, prompt 01) e configuração lida do ambiente."""

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

SCHEMA = "base_tesouro_nacional"
TIMEZONE = "America/Sao_Paulo"
USER_AGENT = "transferepro-cauc-ingest/0.1"

CKAN_API = "https://www.tesourotransparente.gov.br/ckan/api/3/action"
CKAN_DATASET = "cauc"
SICONFI_ENTES_URL = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/entes"
LINK_CONSULTA_OFICIAL = "https://www.tesourotransparente.gov.br/ckan/dataset/cauc"

TIPOS = ("ESTADO", "MUNICIPIO")

# tipo_ente -> (resource_id, trecho do nome/URL usado no fallback via package_show)
RECURSOS = {
    "MUNICIPIO": ("07af297a-5e59-494a-a88a-55ddfd2f4b01", "municipios"),
    "ESTADO": ("83d2fbd7-4369-4935-85c2-366925cc5841", "estados-e-df"),
}
URL_FIXA = {
    "MUNICIPIO": "https://www.tesourotransparente.gov.br/ckan/dataset/72b5f371-0c35-4613-8076-c99c821a6410"
    "/resource/07af297a-5e59-494a-a88a-55ddfd2f4b01/download/"
    "relatorio-situacao-de-varios-entes---municipios---uf-todas---abrangencia-1.csv",
}
TAMANHO_MINIMO = {"MUNICIPIO": 1_000_000, "ESTADO": 5_000}
FAIXA_ENTES = {"MUNICIPIO": (5560, 5575), "ESTADO": (27, 27)}
TIPO_ENTE_ARQUIVO = {"Municípios": "MUNICIPIO", "Estados e DF": "ESTADO"}

COLUNAS_FIXAS = ["UF", "Nome do Ente Federado", "Código IBGE", "Código SIAFI", "Região", "População", "Fonte"]

UF_IBGE = {
    "RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16", "TO": "17",
    "MA": "21", "PI": "22", "CE": "23", "RN": "24", "PB": "25", "PE": "26", "AL": "27", "SE": "28", "BA": "29",
    "MG": "31", "ES": "32", "RJ": "33", "SP": "35", "PR": "41", "SC": "42", "RS": "43",
    "MS": "50", "MT": "51", "GO": "52", "DF": "53",
}
ITENS_ESTADO_APENAS = {"4.3", "5.8"}
# Seed de cauc_dim_item (legenda de 30/10/2026). Mudanças posteriores entram via aprovar-item / LEG_DESCRICAO_MUDOU.
ITENS_SEED = {
    "1.1": "Regularidade quanto a Tributos, a Contribuições Previdenciárias Federais e à Dívida Ativa da União",
    "1.2": "Regularidade no pagamento de precatórios judiciais",
    "1.3": "Regularidade quanto a Contribuições para o FGTS",
    "1.4": "Regularidade em relação à Adimplência Financeira em Empréstimos e Financiamentos concedidos pela União",
    "1.5": "Regularidade perante o Poder Público Federal",
    "2.1.1": "SIAFI/Subsistema Transferências",
    "2.1.2": "Transferegov.br",
    "3.1.1": "Publicação do Relatório de Gestão Fiscal",
    "3.1.2": "Encaminhamento do Relatório de Gestão Fiscal ao Siconfi",
    "3.2.1": "Publicação do Relatório Resumido de Execução Orçamentária - RREO",
    "3.2.2": "Encaminhamento do Relatório Resumido de Execução Orçamentária ao Siconfi",
    "3.2.3": "Encaminhamento do Anexo 8 do Relatório Resumido de Execução Orçamentária ao Siope",
    "3.2.4": "Encaminhamento do Anexo 12 do Relatório Resumido de Execução Orçamentária ao Siops",
    "3.3": "Encaminhamento das Contas Anuais",
    "3.4.1": "Encaminhamento da Matriz de Saldos Contábeis Mensal",
    "3.4.2": "Encaminhamento da Matriz de Saldos Contábeis de Encerramento",
    "3.5": "Encaminhamento de Informações para o Cadastro da Dívida Pública - CDP",
    "3.6": "Transparência da execução orçamentária e financeira em meio eletrônico de acesso público",
    "3.7": "Adoção de Sistema Integrado de Administração Financeira e Controle - Siafic",
    "4.1": "Exercício da Plena Competência Tributária",
    "4.2": "Regularidade Previdenciária",
    "4.3": "Regularidade quanto à Concessão de Incentivos Fiscais",
    "5.1": "Aplicação Mínima de recursos em Educação",
    "5.2": "Aplicação Mínima de recursos em Saúde",
    "5.3": "Limite de Despesas com Parcerias Público-Privadas - PPP",
    "5.4": "Limite de operações de crédito, inclusive por antecipação de receita",
    "5.5": "Regularidade na aplicação mínima do Fundeb para pagamento de profissionais da educação básica",
    "5.6": "Regularidade na aplicação mínima da complementação da União ao Fundeb em despesas de capital",
    "5.7": "Regularidade na aplicação de 50% da complementação VAAT do Fundeb na educação infantil",
    "5.8": "Regularidade na destinação mínima de recursos para a constituição do Fundeb",
}
GRUPOS = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V"}


def item_seed(codigo: str, descricao: str, aplica_municipio: bool, aplica_estado: bool) -> dict:
    """Linha de cauc_dim_item: grupo pelo 1º nível, subgrupo pelos 2 primeiros quando o código tem 3 níveis."""
    partes = codigo.split(".")
    return {
        "codigo_item": codigo,
        "grupo": GRUPOS.get(partes[0], partes[0]),
        "subgrupo": ".".join(partes[:2]) if len(partes) == 3 else None,
        "descricao": descricao,
        "aplica_municipio": aplica_municipio,
        "aplica_estado": aplica_estado,
    }


def itens_seed() -> dict[str, dict]:
    return {c: item_seed(c, d, c not in ITENS_ESTADO_APENAS, True) for c, d in ITENS_SEED.items()}


UF_REGIAO = {uf:{"1": "N", "2": "NE", "3": "SE", "4": "S", "5": "CO"}[cod[0]] for uf, cod in UF_IBGE.items()}


@dataclass(frozen=True)
class Config:
    db_host: str
    db_porta: int
    db_nome: str
    db_usuario: str
    db_senha: str = field(repr=False)
    pasta_dados: Path = Path("data")
    feriados_facultativos: bool = True
    feriados_extras: tuple[date, ...] = ()
    janela_vencimento_dias: int = 15
    alerta_vencimento_ativo: bool = False
    dq_limites_json: str = ""

    @property
    def pasta_raw(self) -> Path:
        return self.pasta_dados / "raw"

    @property
    def pasta_tmp(self) -> Path:
        return self.pasta_dados / "tmp"

    @property
    def pasta_relatorios(self) -> Path:
        return self.pasta_dados / "relatorios"

    @property
    def pasta_alertas(self) -> Path:
        return self.pasta_dados / "alertas"


def _bool(nome: str, padrao: bool) -> bool:
    return os.environ.get(nome, str(padrao)).strip().lower() in ("1", "true", "sim", "yes")


def carregar() -> Config:
    """Lê o .env da raiz."""
    load_dotenv()
    env = os.environ
    nome = env.get("TRANSFEREPRO_DB_NAME")
    if not env.get("TRANSFEREPRO_DB_USER") or not nome:
        raise RuntimeError("Defina TRANSFEREPRO_DB_USER/PASSWORD/NAME no .env (veja .env.example)")
    extras = tuple(date.fromisoformat(d.strip()) for d in env.get("CAUC_FERIADOS_EXTRAS", "").split(",") if d.strip())
    return Config(
        db_host=env.get("TRANSFEREPRO_DB_HOST", "localhost"),
        db_porta=int(env.get("TRANSFEREPRO_DB_PORT", "5432")),
        db_nome=nome,
        db_usuario=env["TRANSFEREPRO_DB_USER"],
        db_senha=env.get("TRANSFEREPRO_DB_PASSWORD", ""),
        pasta_dados=Path(env.get("CAUC_DATA_DIR", "data")),
        feriados_facultativos=_bool("CAUC_FERIADOS_FACULTATIVOS", True),
        feriados_extras=extras,
        janela_vencimento_dias=int(env.get("CAUC_ALERTA_JANELA_VENCIMENTO_DIAS", "15")),
        alerta_vencimento_ativo=_bool("CAUC_ALERTA_VENCIMENTO_ATIVO", False),
        dq_limites_json=env.get("CAUC_DQ_LIMITES_JSON", ""),
    )
