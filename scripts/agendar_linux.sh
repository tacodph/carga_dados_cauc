#!/usr/bin/env bash
# Cria/atualiza o agendamento da carga do CAUC em um servidor Linux.
#
# Dispara todo dia às 08:00, 12:00 e 18:00 (America/Sao_Paulo). A própria carga decide se está na janela (primeiro
# dia útil da semana e 08h do dia útil seguinte, com feriados nacionais e pontos facultativos) e se a versão da semana
# já foi publicada; fora disso termina em segundos com código 0. Execução concorrente sai com código 2.
#
# Uso:
#   sudo bash scripts/agendar_linux.sh                    # systemd timer (padrão; exige root)
#   sudo bash scripts/agendar_linux.sh --usuario cauc     # usuário que executa a carga (padrão: quem chamou o sudo)
#        bash scripts/agendar_linux.sh --cron             # crontab do usuário atual, sem root
#   sudo bash scripts/agendar_linux.sh --remover          # remove o timer (com --cron, remove a entrada do crontab)
#
# O script NÃO grava senha nem toca no .env: configure o .env do projeto antes.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODO=systemd
REMOVER=0
USUARIO="${SUDO_USER:-$(id -un)}"
HORARIOS=(08:00 12:00 18:00)
TZ_NEGOCIO="America/Sao_Paulo"
UNIDADE=cauc-carga

while [ $# -gt 0 ]; do
    case "$1" in
        --cron) MODO=cron ;;
        --systemd) MODO=systemd ;;
        --usuario) USUARIO="${2:?--usuario exige um nome}"; shift ;;
        --remover) REMOVER=1 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "Argumento desconhecido: $1 (use --help)" >&2; exit 2 ;;
    esac
    shift
done

verificar_projeto() {
    [ -x "$RAIZ/.venv/bin/cauc-ingest" ] || { echo "Ambiente virtual ausente em $RAIZ/.venv (veja COMANDOS.md)." >&2; exit 1; }
    [ -f "$RAIZ/.env" ] || { echo "Arquivo $RAIZ/.env ausente: copie .env.example e preencha." >&2; exit 1; }
}

chmod +x "$RAIZ/scripts/executar_carga.sh" "$RAIZ/scripts/agendar_linux.sh"

# ------------------------------------------------------------------ systemd
if [ "$MODO" = systemd ]; then
    [ "$(id -u)" -eq 0 ] || { echo "O modo systemd exige root: use sudo (ou --cron)." >&2; exit 1; }
    command -v systemctl >/dev/null || { echo "systemd não encontrado: use --cron." >&2; exit 1; }
    SERVICO="/etc/systemd/system/$UNIDADE.service"
    TIMER="/etc/systemd/system/$UNIDADE.timer"

    if [ "$REMOVER" -eq 1 ]; then
        systemctl disable --now "$UNIDADE.timer" 2>/dev/null || true
        rm -f "$SERVICO" "$TIMER"
        systemctl daemon-reload
        echo "Timer $UNIDADE removido."
        exit 0
    fi

    id "$USUARIO" >/dev/null 2>&1 || { echo "Usuário '$USUARIO' não existe." >&2; exit 1; }
    verificar_projeto

    cat > "$SERVICO" <<UNIT
[Unit]
Description=Carga semanal do CAUC (Tesouro Nacional -> transferepro.base_tesouro_nacional)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USUARIO
WorkingDirectory=$RAIZ
ExecStart=$RAIZ/scripts/executar_carga.sh
TimeoutStartSec=2h
Nice=10
# 2 = outra carga em execução: não conta como falha da unidade
SuccessExitStatus=2
UNIT

    {
        echo "[Unit]"
        echo "Description=Agenda a carga do CAUC (${HORARIOS[*]} $TZ_NEGOCIO)"
        echo
        echo "[Timer]"
        for h in "${HORARIOS[@]}"; do echo "OnCalendar=*-*-* $h:00 $TZ_NEGOCIO"; done
        echo "Persistent=true"
        echo
        echo "[Install]"
        echo "WantedBy=timers.target"
    } > "$TIMER"

    systemctl daemon-reload
    systemctl enable --now "$UNIDADE.timer"
    echo "Timer '$UNIDADE' ativo (${HORARIOS[*]} $TZ_NEGOCIO), executando como '$USUARIO'."
    echo "  Próximas execuções : systemctl list-timers $UNIDADE.timer"
    echo "  Disparo manual     : sudo systemctl start $UNIDADE.service"
    echo "  Logs               : journalctl -u $UNIDADE.service -n 100   (e $RAIZ/logs/)"
    exit 0
fi

# --------------------------------------------------------------------- cron
MARCA="# $UNIDADE"
# CAUC_TESTE_FUSO="-0300,+0000" (negócio,servidor) só mostra as linhas calculadas, sem tocar no crontab
if [ -z "${CAUC_TESTE_FUSO:-}" ]; then
    command -v crontab >/dev/null || { echo "crontab não encontrado (instale o pacote 'cron')." >&2; exit 1; }
    ALVO=(crontab)
    [ "$(id -u)" -eq 0 ] && ALVO=(crontab -u "$USUARIO")
    ATUAL="$("${ALVO[@]}" -l 2>/dev/null | grep -vF "$MARCA" || true)"

    if [ "$REMOVER" -eq 1 ]; then
        printf '%s\n' "$ATUAL" | "${ALVO[@]}" -
        echo "Entrada do cron removida."
        exit 0
    fi
    verificar_projeto
    mkdir -p "$RAIZ/logs"
    Z_NEGOCIO="$(TZ=$TZ_NEGOCIO date +%z)"
    Z_LOCAL="$(date +%z)"
    [ "$Z_NEGOCIO" != "+0000" ] || { echo "tzdata ausente: instale com 'sudo apt install -y tzdata'." >&2; exit 1; }
else
    Z_NEGOCIO="${CAUC_TESTE_FUSO%%,*}"; Z_LOCAL="${CAUC_TESTE_FUSO##*,}"
fi

# O cron usa o fuso do servidor (em geral UTC): converte os horários de Brasília. Como a carga roda todo dia e a
# própria CLI aplica a janela de dias úteis, não importa se a conversão cair no dia anterior/seguinte.
minutos_offset() {  # "+HHMM"/"-HHMM" -> minutos
    local z="$1" sinal=1
    [ "${z:0:1}" = "-" ] && sinal=-1
    echo $(( sinal * (10#${z:1:2} * 60 + 10#${z:3:2}) ))
}
OFF_NEGOCIO="$(minutos_offset "$Z_NEGOCIO")"
OFF_LOCAL="$(minutos_offset "$Z_LOCAL")"

LINHAS=()
for h in "${HORARIOS[@]}"; do
    total=$(( (10#${h%%:*} * 60 + 10#${h##*:} + OFF_LOCAL - OFF_NEGOCIO + 1440) % 1440 ))
    LINHAS+=("$((total % 60)) $((total / 60)) * * * $RAIZ/scripts/executar_carga.sh >> $RAIZ/logs/cron.log 2>&1 $MARCA")
done

if [ -n "${CAUC_TESTE_FUSO:-}" ]; then printf '%s\n' "${LINHAS[@]}"; exit 0; fi

{ printf '%s\n' "$ATUAL"; printf '%s\n' "${LINHAS[@]}"; } | sed '/^$/d' | "${ALVO[@]}" -
echo "Cron instalado para '$USUARIO' (Brasília ${HORARIOS[*]}):"
printf '  %s\n' "${LINHAS[@]}"
echo "  Conferir: crontab -l   |   Log: $RAIZ/logs/cron.log"
