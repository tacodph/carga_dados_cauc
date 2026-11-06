"""Linha de comando: cauc-ingest <comando>. Alertas funcionam só em dry-run (gravados, nunca enviados)."""

import argparse
import sys
from datetime import date
from pathlib import Path

from . import config, db, pipeline
from .config import item_seed
from .db import q


def _conn():
    return db.conectar(config.carregar())


def cmd_run(a) -> int:
    return pipeline.executar(config.carregar(), forcar=a.forcar, tipos=tuple(a.tipo) if a.tipo else config.TIPOS)


def cmd_reprocess(a) -> int:
    print(pipeline.reprocessar(config.carregar(), a.tipo, date.fromisoformat(a.data)))
    return 0


def cmd_backfill(a) -> int:
    for arquivo, resultado in pipeline.backfill(config.carregar(), Path(a.pasta)).items():
        print(f"{resultado:14} {arquivo}")
    return 0


def cmd_aprovar_item(a) -> int:
    if not (a.municipio or a.estado):
        sys.exit("Informe --municipio e/ou --estado")
    linha = item_seed(a.codigo, a.descricao, a.municipio, a.estado)
    with _conn() as c:
        c.execute(q("""insert into {s}.cauc_dim_item (codigo_item, grupo, subgrupo, descricao, aplica_municipio, aplica_estado,
                       vigente_desde) values (%(codigo_item)s, %(grupo)s, %(subgrupo)s, %(descricao)s, %(aplica_municipio)s,
                       %(aplica_estado)s, current_date)
                       on conflict (codigo_item) do update set descricao = excluded.descricao,
                       aplica_municipio = excluded.aplica_municipio, aplica_estado = excluded.aplica_estado,
                       vigente_ate = null"""), linha)
    print(f"Item {a.codigo} aprovado. Reprocesse: cauc-ingest reprocess --tipo <TIPO> --data <AAAA-MM-DD>")
    return 0


def cmd_remover_item(a) -> int:
    with _conn() as c:
        c.execute(q("update {s}.cauc_dim_item set vigente_ate = current_date where codigo_item = %s"), (a.codigo,))
    print(f"Item {a.codigo} encerrado (vigente_ate = hoje). Reprocesse a versão rejeitada.")
    return 0


def cmd_validar_match(a) -> int:
    with _conn() as c:
        n = c.execute(q("""update {s}.cauc_map_entidade set validado_por = %s, validado_em = now()
                           where id_entidade_sistema = %s"""), (a.por, a.cnpj)).rowcount
    print("validado" if n else "CNPJ sem match registrado")
    return 0 if n else 1


def cmd_silenciar(a) -> int:
    with _conn() as c:
        c.execute(q("""insert into {s}.cauc_alerta_silencio (id_entidade_sistema, codigo_item, tipo, ate, motivo)
                       values (%s, %s, %s, %s, %s)"""), (a.cnpj, a.item, a.tipo_alerta, date.fromisoformat(a.ate), a.motivo))
    print("silêncio registrado")
    return 0


def cmd_distribuicao_vencimento(a) -> int:
    """Base para decidir em quais itens o alerta VENCIMENTO_PROXIMO é informativo (prompt 09)."""
    with _conn() as c:
        linhas = c.execute(q("""
            select tipo_ente, codigo_item, count(*) as regulares, count(distinct data_validade) as datas_distintas,
                   count(*) filter (where dias_para_vencer = 0) as vence_na_data_ref,
                   count(*) filter (where dias_para_vencer between 1 and 15) as vence_1_15,
                   count(*) filter (where dias_para_vencer between 16 and 30) as vence_16_30,
                   percentile_disc(0.5) within group (order by dias_para_vencer) as mediana_dias
            from {s}.cauc_fato_situacao_item
            where status = 'REGULAR' and (tipo_ente, data_referencia) in
                  (select tipo_ente, max(data_referencia) from {s}.cauc_fato_ente group by 1)
            group by 1, 2 order by 1, string_to_array(codigo_item, '.')::int[]""")).fetchall()
    print("tipo;item;regulares;datas_distintas;vence_na_data_ref;vence_1_15;vence_16_30;mediana_dias")
    for linha in linhas:
        print(";".join(str(v) for v in linha))
    return 0


def cmd_ddl(a) -> int:
    print(db.ddl())
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cauc-ingest", description=__doc__)
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("run", help="execução agendada (respeita a janela de dias úteis)")
    s.add_argument("--forcar", action="store_true", help="ignora janela e 'semana já publicada'")
    s.add_argument("--tipo", action="append", choices=config.TIPOS)
    s.set_defaults(f=cmd_run)

    s = sub.add_parser("reprocess", help="reprocessa uma versão a partir do raw")
    s.add_argument("--tipo", required=True, choices=config.TIPOS)
    s.add_argument("--data", required=True, help="data de referência AAAA-MM-DD")
    s.set_defaults(f=cmd_reprocess)

    s = sub.add_parser("backfill", help="carrega CSVs antigos de uma pasta, em ordem de data")
    s.add_argument("--pasta", required=True)
    s.set_defaults(f=cmd_backfill)

    s = sub.add_parser("aprovar-item", help="inclui/atualiza item em cauc_dim_item (item novo no CAUC)")
    s.add_argument("codigo")
    s.add_argument("--descricao", required=True)
    s.add_argument("--municipio", action="store_true", help="aplica a municípios")
    s.add_argument("--estado", action="store_true", help="aplica a estados/DF")
    s.set_defaults(f=cmd_aprovar_item)

    s = sub.add_parser("remover-item", help="encerra item que saiu do CAUC")
    s.add_argument("codigo")
    s.set_defaults(f=cmd_remover_item)

    s = sub.add_parser("validar-match", help="valida manualmente o match de uma entidade (CNPJ)")
    s.add_argument("cnpj")
    s.add_argument("--por", required=True, help="quem validou")
    s.set_defaults(f=cmd_validar_match)

    s = sub.add_parser("silenciar", help="silencia alertas até uma data")
    s.add_argument("--ate", required=True)
    s.add_argument("--cnpj")
    s.add_argument("--item")
    s.add_argument("--tipo-alerta")
    s.add_argument("--motivo")
    s.set_defaults(f=cmd_silenciar)

    sub.add_parser("distribuicao-vencimento", help="dias para vencer por item (última versão)").set_defaults(
        f=cmd_distribuicao_vencimento)
    sub.add_parser("ddl", help="imprime a DDL (fonte da migration do Laravel)").set_defaults(f=cmd_ddl)

    a = p.parse_args(argv)
    pipeline.configurar_logs(Path("logs"))
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
