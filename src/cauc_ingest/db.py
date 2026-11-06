"""Conexão e operações de banco comuns às etapas."""

import json
from datetime import date
from importlib.resources import files

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from .config import SCHEMA, Config, itens_seed
from .validate import Resultado


def conectar(cfg: Config) -> psycopg.Connection:
    """Autocommit; transações explícitas com `conn.transaction()`."""
    return psycopg.connect(host=cfg.db_host, port=cfg.db_porta, dbname=cfg.db_nome, user=cfg.db_usuario,
                           password=cfg.db_senha, autocommit=True, application_name="cauc-ingest")


def q(texto: str) -> sql.SQL:
    """SQL com `{s}` trocado pelo schema fixo (constante, não vem de entrada externa)."""
    return sql.SQL(texto.replace("{s}", SCHEMA))


def ddl() -> str:
    return files("cauc_ingest").joinpath("sql/ddl.sql").read_text(encoding="utf-8")


def garantir_dim_item(conn) -> None:
    """Seed inicial; nunca sobrescreve itens já existentes (aprovação humana prevalece)."""
    with conn.cursor() as cur:
        cur.executemany(q("""insert into {s}.cauc_dim_item (codigo_item, grupo, subgrupo, descricao, aplica_municipio,
                             aplica_estado, vigente_desde) values (%(codigo_item)s, %(grupo)s, %(subgrupo)s,
                             %(descricao)s, %(aplica_municipio)s, %(aplica_estado)s, null)
                             on conflict (codigo_item) do nothing"""), list(itens_seed().values()))


def carregar_dim_item(conn) -> dict[str, dict]:
    cur = conn.execute(q("""select codigo_item, descricao, aplica_municipio, aplica_estado, critico_negocio
                            from {s}.cauc_dim_item where vigente_ate is null"""))
    return {r[0]: {"descricao": r[1], "aplica_municipio": r[2], "aplica_estado": r[3], "critico_negocio": r[4]}
            for r in cur}


def gravar_dq(conn, run_id, id_carga, resultados: list[Resultado]) -> None:
    with conn.cursor() as cur:
        cur.executemany(q("""insert into {s}.cauc_dq_resultado (run_id, id_carga, regra, severidade, passou,
                             valor_observado, limite, detalhe) values (%s, %s, %s, %s, %s, %s, %s, %s)"""),
                        [(run_id, id_carga, r.regra, r.severidade, r.passou, r.valor_observado, r.limite,
                          Jsonb({**r.detalhe, "mensagem": r.mensagem}, dumps=lambda o: json.dumps(o, default=str)))
                         for r in resultados])


def ultima_publicada(conn, tipo: str, antes_de: date | None = None) -> date | None:
    filtro = "and data_referencia < %s" if antes_de else ""
    args = (tipo, antes_de) if antes_de else (tipo,)
    linha = conn.execute(q(f"""select max(data_referencia) from {{s}}.cauc_carga_arquivo
                               where tipo_ente = %s and status = 'PUBLICADO' {filtro}"""), args).fetchone()
    return linha[0]
