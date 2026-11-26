# Nodo Chile en Windows: corre el radar SOLO para tiendas que bloquean a GitHub (hoy: Paris)
# y sube lo leído a la rama `node-data`. La nube lo integra en cada ventana (alertas, historial, app).
#
# Instalación:   powershell -ExecutionPolicy Bypass -File tools\node_windows.ps1 -Install
# Desinstalar:   powershell -ExecutionPolicy Bypass -File tools\node_windows.ps1 -Uninstall
# Probar 1 vez:  powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\radar-precios-node\repo\tools\node_windows.ps1" -Once -Minutes 3
# (La tarea programada "RadarPreciosNodo" lo inicia oculto al iniciar sesión. Si el PC está apagado, la nube
#  sigue igual: Paris llega vía SoloTodo con ~1 h de desfase.)
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Once,
    [string]$Sites = "paris",
    [int]$Minutes = 12
)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Base = Join-Path $env:USERPROFILE "radar-precios-node"
$Repo = Join-Path $Base "repo"
$Venv = Join-Path $Base "venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Log = Join-Path $Base "node.log"
$TaskName = "RadarPreciosNodo"
$Origin = "https://github.com/DiCloudd/radar-precios-cl.git"

function Write-Log([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $Log -Value $line -Encoding UTF8
    if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 5MB)) { Move-Item -Force $Log "$Log.1" }
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like "*radar-precios-node*node_windows.ps1*" -and $_.ProcessId -ne $PID } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Output "Nodo desinstalado (la carpeta $Base se conserva; puedes borrarla)."
    exit 0
}

if ($Install) {
    New-Item -ItemType Directory -Force -Path $Base | Out-Null
    if (-not (Test-Path (Join-Path $Repo ".git"))) { git clone -q $Origin $Repo }
    if (-not (Test-Path $Py)) {
        python -m venv $Venv
        & $Py -m pip install -q --upgrade pip
    }
    & $Py -m pip install -q -r (Join-Path $Repo "requirements.txt")
    $script = Join-Path $Repo "tools\node_windows.ps1"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Sites $Sites"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
        -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Radar de precios (tecnologia): nodo Chile ($Sites)" -Force | Out-Null
    Write-Output "Instalado. Tarea '$TaskName' creada. Log: $Log"
    exit 0
}

# ---------------- bucle del nodo ----------------
Set-Location $Repo
$env:NODE_MODE = "1"                 # solo las tiendas del nodo; nunca publica la app ni manda Telegram
$env:NODE_SITES = $Sites
$env:DATA_DIR = Join-Path $Base "data"
$env:MONITOR_RUNNER = "chile"
$env:TELEGRAM_BOT_TOKEN = ""
$env:BROWSER_FALLBACK = "false"
$env:PYTHONIOENCODING = "utf-8"
$Out = Join-Path $Base "out"
New-Item -ItemType Directory -Force -Path $Out | Out-Null
Write-Log "nodo iniciado (tiendas: $Sites)"

do {
    git -C $Repo pull -q --ff-only origin main 2>&1 | Out-Null
    & $Py main.py --loop --duration $Minutes 2>&1 |
        Select-String -Pattern " OK | ERR |bloqueo|BLOQUEO|pausa|Traceback|Error" | ForEach-Object { Write-Log $_.Line }
    $file = Join-Path $Out "node.json.gz"
    & $Py node.py export $file 2>&1 | ForEach-Object { Write-Log $_ }

    # subir a la rama node-data (un solo commit, force-push: el historial completo vive en la nube)
    $tmp = Join-Path $Out "push"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    Copy-Item $file (Join-Path $tmp "node.json.gz")
    git -C $tmp init -q -b node-data 2>&1 | Out-Null
    git -C $tmp add node.json.gz 2>&1 | Out-Null
    git -C $tmp -c user.name="radar-node" -c user.email="radar-node@users.noreply.github.com" `
        commit -q -m ("nodo chile " + (Get-Date -Format "s")) 2>&1 | Out-Null
    $push = git -C $tmp push -q --force $Origin node-data 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Log "subido a node-data" } else { Write-Log "no se pudo subir: $push" }

    if (-not $Once) { Start-Sleep -Seconds 30 }
} while (-not $Once)
