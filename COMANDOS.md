# Comandos · Carga do CAUC

Todos os comandos em **PowerShell**, na pasta da carga:

```powershell
cd D:\ProjetosSandro\transfere_pro\APIS\carga_tesouro_nacional\Cauc
```

## 1. Instalação (uma vez)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
copy .env.example .env      # preencher TRANSFEREPRO_DB_USER e TRANSFEREPRO_DB_PASSWORD
```

Tabelas e visões `cauc_*` (já aplicado em 05/11/2026; só repita num banco novo). Aplica **apenas** a migration do CAUC:

```powershell
cd D:\projetos_herd\transferepro
php artisan migrate --path=database/migrations/base_tesouro_nacional/2026_11_05_120000_create_cauc_objects.php
cd D:\ProjetosSandro\transfere_pro\APIS\carga_tesouro_nacional\Cauc
```

## 2. Carga completa (manual)

Baixa estados e municípios do Tesouro, valida, publica, refaz o match e gera os alertas (dry-run):

```powershell
.\scripts\executar_carga.ps1 --forcar
```

Equivalente direto, sem gravar log em `logs\execucao_*.txt`:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.venv\Scripts\cauc-ingest.exe run --forcar
```

Só um tipo de ente:

```powershell
.\scripts\executar_carga.ps1 --forcar --tipo ESTADO
.\scripts\executar_carga.ps1 --forcar --tipo MUNICIPIO
```

Sem `--forcar` a carga respeita a janela (primeiro dia útil da semana às 08h/12h/18h e dia útil seguinte às 08h) e
não roda de novo se a versão da semana já foi publicada. Rodar com a mesma versão no portal não grava nada (`SEM_NOVIDADE`).

Códigos de saída: `0` ok / sem novidade / fora da janela · `1` erro · `2` outra execução em andamento · `3` parcial (versão rejeitada ou erro).

## 3. Agendamento (Agendador de Tarefas do Windows)

Abrir o PowerShell **como Administrador** na pasta da carga:

```powershell
.\scripts\agendar_tarefa.ps1                 # roda mesmo sem usuário logado (pede a senha do Windows)
.\scripts\agendar_tarefa.ps1 -SomenteLogado  # alternativa: só roda com o usuário conectado
```

Cria a tarefa **"CAUC - Carga semanal"**: dias úteis às 08:00, 12:00 e 18:00. A própria carga decide se está na janela
(feriados nacionais e pontos facultativos considerados) e encerra em segundos fora dela.

```powershell
Get-ScheduledTask -TaskName 'CAUC - Carga semanal' | Get-ScheduledTaskInfo   # última/próxima execução e resultado
Start-ScheduledTask -TaskName 'CAUC - Carga semanal'                         # dispara agora (respeita a janela)
.\scripts\agendar_tarefa.ps1 -Remover                                        # remove a tarefa
```

## 3b. Linux (rodar e agendar)

Requisitos: Python 3.11+, `python3-venv`, `tzdata`, acesso ao PostgreSQL do transferepro. Na pasta da carga:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env                 # preencher TRANSFEREPRO_DB_HOST/PORT/USER/PASSWORD
chmod +x scripts/*.sh
```

Carga completa manual (mesmos argumentos e códigos de saída do Windows):

```bash
./scripts/executar_carga.sh --forcar
./scripts/executar_carga.sh --forcar --tipo ESTADO
```

Agendamento: dispara todo dia às 08:00, 12:00 e 18:00 de Brasília; a carga aplica a janela de dias úteis e feriados.

```bash
sudo bash scripts/agendar_linux.sh                  # systemd timer "cauc-carga" (recomendado)
sudo bash scripts/agendar_linux.sh --usuario cauc   # executa como outro usuário
bash scripts/agendar_linux.sh --cron                # alternativa sem root: crontab do usuário (converte o fuso)
sudo bash scripts/agendar_linux.sh --remover        # remove o timer (com --cron, remove a linha do crontab)
```

Acompanhar:

```bash
systemctl list-timers cauc-carga.timer          # próximas execuções
sudo systemctl start cauc-carga.service         # dispara agora (respeita a janela)
journalctl -u cauc-carga.service -n 100         # log do systemd (também em logs/)
crontab -l; tail -n 100 logs/cron.log           # se usou --cron
```

Os comandos de manutenção da seção 5 valem no Linux trocando `.venv\Scripts\cauc-ingest.exe` por `.venv/bin/cauc-ingest`.

## 4. Acompanhamento

| O quê | Onde |
|---|---|
| Log da execução | `logs\execucao_AAAAMMDD_HHMMSS.txt` e `logs\cauc_AAAAMMDD.jsonl` |
| Relatório de qualidade | `data\relatorios\dq_report_<TIPO>_<data>.md` |
| Match pendente de validação | `data\relatorios\relatorio_match.csv` |
| Alertas gerados (dry-run) | `data\alertas\alertas_<run_id>.txt` e tabela `cauc_alerta` |
| Histórico de execuções | tabela `base_tesouro_nacional.cauc_execucao` |

```sql
select tipo_ente, status, data_referencia, inicio, fim, erro
from base_tesouro_nacional.cauc_execucao order by inicio desc limit 10;
```

## 5. Comandos de manutenção

```powershell
.venv\Scripts\cauc-ingest.exe reprocess --tipo MUNICIPIO --data 2026-11-05     # refaz uma versão a partir do raw
.venv\Scripts\cauc-ingest.exe backfill --pasta data\raw                        # recarrega arquivos antigos
.venv\Scripts\cauc-ingest.exe aprovar-item 5.9 --descricao "..." --municipio --estado   # item novo no CAUC
.venv\Scripts\cauc-ingest.exe validar-match 05182233000176 --por "Sandro"      # valida sugestão de match
.venv\Scripts\cauc-ingest.exe silenciar --ate 2026-12-31 --cnpj 05182233000176 --item 4.2 --motivo "..."
.venv\Scripts\cauc-ingest.exe distribuicao-vencimento                          # base para ligar alerta de vencimento
.venv\Scripts\cauc-ingest.exe --help
```

## 6. Testes

```powershell
.venv\Scripts\pytest                         # unitários (não tocam no banco)
```

Os testes de integração **truncam todas as tabelas `cauc_*`**. Só rode se puder recarregar logo depois:

```powershell
$env:CAUC_TESTE_BANCO = '1'; .venv\Scripts\pytest -m integracao; Remove-Item Env:CAUC_TESTE_BANCO
.\scripts\executar_carga.ps1 --forcar        # recarrega a versão atual
```

Detalhes de incidentes: `docs\RUNBOOK-cauc.md`. Decisões de arquitetura: `docs\ADR-001-cauc.md`.
