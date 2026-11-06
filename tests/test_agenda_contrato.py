from datetime import date, datetime
from pathlib import Path

import pytest

from cauc_ingest import db, pipeline
from cauc_ingest.config import Config

CFG = Config("h", 1, "db", "u", "s")
LARAVEL_DDL = Path(r"D:\projetos_herd\transferepro\database\migrations\base_tesouro_nacional\sql\cauc_ddl.sql")


def em(ano, mes, dia, hora=8):
    return datetime(ano, mes, dia, hora, tzinfo=pipeline.TZ)


def test_semana_normal_segunda_e_terca_8h():
    assert pipeline.janela(em(2026, 11, 9), CFG) == "PRIMEIRO_DIA"        # segunda
    assert pipeline.janela(em(2026, 11, 9, 18), CFG) == "PRIMEIRO_DIA"
    assert pipeline.janela(em(2026, 11, 10), CFG) == "DIA_SEGUINTE"       # terça 08h (último disparo)
    assert pipeline.janela(em(2026, 11, 10, 12), CFG) is None
    assert pipeline.janela(em(2026, 11, 11), CFG) is None


def test_segunda_feriado_executa_terca():
    # 2027-02-08 (segunda) e 09 são Carnaval (ponto facultativo); 10 é Quarta de Cinzas (facultativo até 14h)
    assert pipeline.janela(em(2027, 2, 8), CFG) is None
    assert pipeline.primeiro_dia_util_semana(date(2027, 2, 10), CFG) == date(2027, 2, 11)
    sem_facultativos = Config("h", 1, "db", "u", "s", feriados_facultativos=False)
    assert pipeline.janela(em(2027, 2, 8), sem_facultativos) == "PRIMEIRO_DIA"


def test_segunda_feriado_nacional_terca():
    # 2026-09-07 (segunda) Independência
    assert pipeline.janela(em(2026, 9, 7), CFG) is None
    assert pipeline.janela(em(2026, 9, 8), CFG) == "PRIMEIRO_DIA"
    assert pipeline.janela(em(2026, 9, 9), CFG) == "DIA_SEGUINTE"


def test_feriado_extra_configurado():
    cfg = Config("h", 1, "db", "u", "s", feriados_extras=(date(2026, 11, 9),))
    assert pipeline.janela(em(2026, 11, 10), cfg) == "PRIMEIRO_DIA"


@pytest.mark.skipif(not LARAVEL_DDL.exists(), reason="repositório Laravel não encontrado")
def test_ddl_do_laravel_identica():
    assert LARAVEL_DDL.read_text(encoding="utf-8") == db.ddl()
