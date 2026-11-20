# Trae la última versión del repositorio y actualiza la base, sin perder los datos cargados.
#   powershell -ExecutionPolicy Bypass -File .\actualizar.ps1
# Antes de actualizar, cerrá el sistema (Ctrl+C en la ventana de 'Sistema Bomberos').

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Correr {
    & $args[0] $args[1..($args.Length - 1)]
    if ($LASTEXITCODE -ne 0) { throw "Falló: $($args -join ' ')" }
}

if (netstat -ano | Select-String "127.0.0.1:8000\s+\S+\s+LISTENING") {
    Write-Host "El sistema está abierto. Cerralo (Ctrl+C en su ventana) y volvé a correr este script." -ForegroundColor Red
    exit 1
}
if (git status --porcelain --untracked-files=no) {
    Write-Host "Hay archivos del programa modificados en esta PC; no se actualiza para no pisarlos:" -ForegroundColor Red
    git status --short --untracked-files=no
    exit 1
}

# Copia de la base antes de tocar nada: si algo sale mal, se vuelve a esta.
if (Test-Path ".\db.sqlite3") {
    New-Item -ItemType Directory -Force ".\respaldos" | Out-Null
    $copia = ".\respaldos\db-$(Get-Date -Format 'yyyy-MM-dd_HHmm').sqlite3"
    Copy-Item ".\db.sqlite3" $copia
    Write-Host "Copia de la base: $copia"
}

Correr git pull --ff-only
. .\configuracion_local.ps1
$env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1"
$py = ".\.venv\Scripts\python.exe"
Correr $py -m pip install --quiet -r requirements.txt
Correr $py manage.py migrate
Correr $py manage.py createcachetable
Correr $py manage.py cargar_catalogos
Correr $py manage.py collectstatic --noinput --verbosity 0

Write-Host ""
Write-Host "Actualizado. Ya se puede abrir 'Sistema Bomberos'." -ForegroundColor Green
