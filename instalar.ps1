# Instala el sistema en una PC del cuartel (Windows), después de clonar el repositorio:
#   git clone https://github.com/francovalentin08/bomberos-sistema.git
#   cd bomberos-sistema
#   powershell -ExecutionPolicy Bypass -File .\instalar.ps1 -Listado "C:\ruta\Listado Personal.xlsx"
#
# Hace falta Python 3.13 o más nuevo (python.org, tildando "Add python.exe to PATH") y Git.
# Se puede volver a correr: no borra datos ni duplica nada.

param([string]$Listado, [switch]$SinAccesoDirecto)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Paso($texto) { Write-Host ""; Write-Host "== $texto" -ForegroundColor Cyan }
function Correr {
    & $args[0] $args[1..($args.Length - 1)]
    if ($LASTEXITCODE -ne 0) { throw "Falló: $($args -join ' ')" }
}

Paso "Python"
$python = $null
$prueba = "import sys; print(sys.executable if sys.version_info >= (3, 13) else '')"
foreach ($lanzador in @("py", "python")) {
    try {
        if ($lanzador -eq "py") { $exe = & py -3 -c $prueba 2>$null } else { $exe = & python -c $prueba 2>$null }
        if ($exe) { $python = $exe; break }
    } catch { }
}
if (-not $python) {
    Write-Host "No se encontró Python 3.13 o más nuevo. Instalalo desde https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "(tildá 'Add python.exe to PATH') y volvé a correr este script."
    exit 1
}
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Correr $python -m venv .venv
}
$py = ".\.venv\Scripts\python.exe"
Correr $py -m pip install --quiet --upgrade pip
Correr $py -m pip install --quiet -r requirements.txt

Paso "Configuración de esta PC"
# Clave secreta propia de esta PC (la del repositorio es pública) y sin modo de depuración,
# porque el túnel deja el sistema accesible desde internet.
$config = Join-Path $PSScriptRoot "configuracion_local.ps1"
if (-not (Test-Path $config)) {
    $clave = & $py -c "import secrets; print(secrets.token_urlsafe(50))"
    @(
        "# Generado por instalar.ps1. No se sube al repositorio.",
        "`$env:DJANGO_SECRET_KEY = '$clave'",
        "`$env:DJANGO_DEBUG = '0'",
        "`$env:DJANGO_SOLO_HTTPS = '0'"
    ) | Out-File -FilePath $config -Encoding utf8
    Write-Host "Creado configuracion_local.ps1"
}
. $config
$env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1"

Paso "Base de datos"
Correr $py manage.py migrate --verbosity 0
Correr $py manage.py createcachetable
Correr $py manage.py cargar_catalogos
Correr $py manage.py collectstatic --noinput --verbosity 0

$hayAdmin = & $py manage.py shell -c "from django.contrib.auth.models import User; print(User.objects.filter(is_superuser=True).exists())"
if ($hayAdmin -ne "True") {
    if ($env:DJANGO_SUPERUSER_PASSWORD) {
        Correr $py manage.py createsuperuser --noinput --username admin --email "admin@localhost"
    } else {
        Write-Host "Creá el usuario administrador (para entrar a /admin):" -ForegroundColor Yellow
        Correr $py manage.py createsuperuser
    }
}

Paso "Listado del personal"
if (-not $Listado) {
    $Listado = Get-ChildItem -Path $PSScriptRoot -Filter *.xlsx | Select-Object -First 1 -ExpandProperty FullName
}
if ($Listado -and (Test-Path $Listado)) {
    & $py manage.py importar_listado $Listado --usuarios-prueba
    if ($LASTEXITCODE -ne 0) {
        Write-Host "No se importó el listado. Corregí el Excel y volvé a correr:" -ForegroundColor Yellow
        Write-Host "  powershell -ExecutionPolicy Bypass -File .\instalar.ps1 -Listado `"$Listado`""
    }
} else {
    Write-Host "No se indicó el Excel del listado (-Listado). Se puede importar después volviendo a correr el script." -ForegroundColor Yellow
}

Paso "Túnel para los celulares"
if (-not (Test-Path ".\cloudflared.exe")) {
    $url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $url -OutFile ".\cloudflared.exe" -UseBasicParsing
        Write-Host "Descargado cloudflared.exe"
    } catch {
        Write-Host "No se pudo descargar cloudflared.exe. Bajalo a mano de $url y dejalo en esta carpeta." -ForegroundColor Yellow
    }
}

if (-not $SinAccesoDirecto) {
    Paso "Acceso directo"
    $escritorio = [Environment]::GetFolderPath("Desktop")
    $acceso = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $escritorio "Sistema Bomberos.lnk"))
    $acceso.TargetPath = "powershell.exe"
    $acceso.Arguments = "-ExecutionPolicy Bypass -NoExit -File `"$(Join-Path $PSScriptRoot 'levantar_con_celular.ps1')`""
    $acceso.WorkingDirectory = $PSScriptRoot
    $acceso.Save()
    Write-Host "En el escritorio: 'Sistema Bomberos'."
}

Write-Host ""
Write-Host "Instalación terminada." -ForegroundColor Green
Write-Host "Para usarlo: doble clic en 'Sistema Bomberos' del escritorio."
Write-Host "Para traer cambios nuevos: powershell -ExecutionPolicy Bypass -File .\actualizar.ps1"
