<#
.SYNOPSIS
  Executa a carga do CAUC (cauc-ingest run) e devolve o código de saída da CLI.
.DESCRIPTION
  Usa o .venv da raiz, grava a saída em logs\execucao_AAAAMMDD_HHMMSS.txt e apaga logs com mais de 90 dias.
  Fora da janela (primeiro dia útil da semana e 08h do dia útil seguinte) a CLI sai em segundos com código 0.
  Argumentos extras vão para a CLI, ex.: .\scripts\executar_carga.ps1 --forcar --tipo ESTADO
  Códigos de saída: 0 ok/sem novidade/fora da janela, 1 erro, 2 já em execução, 3 parcial (rejeitado ou erro).
#>
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$exe = Join-Path $raiz '.venv\Scripts\cauc-ingest.exe'
if (-not (Test-Path $exe)) { Write-Error "Ambiente virtual não encontrado em $exe (python -m venv .venv; .venv\Scripts\pip install -e .)"; exit 1 }

$logs = Join-Path $raiz 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$saida = Join-Path $logs ('execucao_{0:yyyyMMdd_HHmmss}.txt' -f (Get-Date))

$env:PYTHONIOENCODING = 'utf-8'
$ErrorActionPreference = 'Continue'
& $exe run @args *>&1 | Tee-Object -FilePath $saida
$codigo = $LASTEXITCODE

Get-ChildItem -Path $logs -File | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-90) } | Remove-Item -Force
exit $codigo
