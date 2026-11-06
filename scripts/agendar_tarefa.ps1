<#
.SYNOPSIS
  Cria/atualiza a tarefa agendada "CAUC - Carga semanal" (dias úteis 08:00, 12:00 e 18:00).
.DESCRIPTION
  O Agendador dispara em todo dia útil; a própria CLI decide se está na janela (primeiro dia útil da semana às
  08h/12h/18h e dia útil seguinte às 08h, com feriados nacionais e pontos facultativos) e se a versão da semana já
  foi publicada. Disparos fora da janela terminam em segundos com código 0.

  Para rodar sem usuário logado, a tarefa precisa das credenciais do Windows: o script pede a senha com
  Get-Credential, usa-a só em memória e não a grava. Use -SomenteLogado para dispensar. Requer PowerShell elevado.
.EXAMPLE
  .\scripts\agendar_tarefa.ps1
  .\scripts\agendar_tarefa.ps1 -SomenteLogado
  .\scripts\agendar_tarefa.ps1 -Remover
#>
param(
    [string]$Usuario = "$env:USERDOMAIN\$env:USERNAME",
    [switch]$SomenteLogado,
    [switch]$Remover
)
$ErrorActionPreference = 'Stop'
$nome = 'CAUC - Carga semanal'

if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false; Write-Host "Tarefa removida."; return }

$raiz = Split-Path -Parent $PSScriptRoot
$script = Join-Path $raiz 'scripts\executar_carga.ps1'
$acao = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $raiz `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$dias = 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'
$gatilhos = '08:00', '12:00', '18:00' | ForEach-Object { New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At $_ }
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 0

if ($SomenteLogado) {
    $principal = New-ScheduledTaskPrincipal -UserId $Usuario -LogonType Interactive
    Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilhos -Settings $config -Principal $principal -Force | Out-Null
} else {
    $cred = Get-Credential -UserName $Usuario -Message 'Conta do Windows que executará a tarefa'
    Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilhos -Settings $config `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
}
Write-Host "Tarefa '$nome' registrada (dias úteis 08:00, 12:00, 18:00; a CLI aplica a janela e os feriados)."
