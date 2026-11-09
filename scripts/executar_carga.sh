#!/usr/bin/env bash
# Executa a carga do CAUC (cauc-ingest run) e devolve o código de saída da CLI.
#
# Usa o .venv da raiz, grava a saída em logs/execucao_AAAAMMDD_HHMMSS.txt e apaga logs com mais de 90 dias.
# Fora da janela (primeiro dia útil da semana e 08h do dia útil seguinte) a CLI sai em segundos com código 0.
# Argumentos extras vão para a CLI, ex.: ./scripts/executar_carga.sh --forcar --tipo ESTADO
# Códigos de saída: 0 ok/sem novidade/fora da janela, 1 erro, 2 já em execução, 3 parcial (rejeitado ou erro).
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

EXE="$RAIZ/.venv/bin/cauc-ingest"
if [ ! -x "$EXE" ]; then
    echo "Ambiente virtual não encontrado em $EXE (python3 -m venv .venv && .venv/bin/pip install -e .)" >&2
    exit 1
fi

mkdir -p logs
SAIDA="logs/execucao_$(date +%Y%m%d_%H%M%S).txt"

export PYTHONIOENCODING=utf-8
"$EXE" run "$@" 2>&1 | tee "$SAIDA"
CODIGO=${PIPESTATUS[0]}

find logs -type f -mtime +90 -delete
exit "$CODIGO"
