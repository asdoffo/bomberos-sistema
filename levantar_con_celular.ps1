# Levanta el sistema para usarlo desde la PC y el celular a la vez:
# túnel HTTPS de Cloudflare (el celular necesita HTTPS para trabajar sin señal) + servidor de Django.
#
# Uso: doble clic en iniciar.bat, o desde la carpeta del proyecto:
#   powershell -ExecutionPolicy Bypass -File .\levantar_con_celular.ps1 [-SoloPC]
# -SoloPC: sin túnel (solo desde esta PC, http://localhost:8000).
# Para terminar: Ctrl+C (se cierran el servidor y el túnel).
# La dirección cambia cada vez que se levanta.
param([switch]$SoloPC)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
# Clave propia y sin depuración en las PC del cuartel (lo crea instalar.ps1).
if (Test-Path ".\configuracion_local.ps1") { . .\configuracion_local.ps1 }
$puerto = 8000
# Siempre el Python del entorno virtual (el "python" suelto de la PC no tiene Django).
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Falta el entorno virtual (.venv). Corré primero instalar.ps1." -ForegroundColor Red
    exit 1
}

# La base al día: después de cada cambio del sistema puede haber migraciones nuevas.
# Sin aplicarlas, el servidor arranca pero las pantallas fallan.
& $py manage.py migrate --check *> $null
if ($LASTEXITCODE -ne 0) {
    if (Test-Path ".\db.sqlite3") {
        New-Item -ItemType Directory -Force ".\respaldos" | Out-Null
        $copia = ".\respaldos\db-$(Get-Date -Format 'yyyy-MM-dd_HHmm').sqlite3"
        Copy-Item ".\db.sqlite3" $copia
        Write-Host "Hay cambios en la base: copia de respaldo en $copia"
    }
    & $py manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { Write-Host "No se pudo actualizar la base (ver el error de arriba)." -ForegroundColor Red; exit 1 }
    & $py manage.py createcachetable
    & $py manage.py cargar_catalogos | Out-Null  # códigos, motivos y roles nuevos; no pisa lo editado
    if ($LASTEXITCODE -ne 0) { Write-Host "No se pudieron cargar los catálogos." -ForegroundColor Red; exit 1 }
}

if ($SoloPC) {
    $env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1"
    Write-Host "  Desde esta PC: http://localhost:$puerto  (Ctrl+C para terminar)" -ForegroundColor Yellow
    & $py manage.py runserver "127.0.0.1:$puerto"
    exit $LASTEXITCODE
}

if (-not (Test-Path ".\cloudflared.exe")) {
    Write-Host "Falta cloudflared.exe en esta carpeta. Bajalo de:" -ForegroundColor Red
    Write-Host "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    exit 1
}
# Dos servidores en el mismo puerto se reparten los pedidos y mezclan los datos.
if (netstat -ano | Select-String "127.0.0.1:$puerto\s+\S+\s+LISTENING") {
    Write-Host "Ya hay algo usando el puerto $puerto (otro runserver?). Cerralo y volvé a probar." -ForegroundColor Red
    exit 1
}

$log = Join-Path $PSScriptRoot "cloudflared.log"
if (Test-Path $log) { Remove-Item $log }
# http2 en lugar de QUIC: se corta menos en redes con VPN o firewall.
$tunel = Start-Process -FilePath ".\cloudflared.exe" -PassThru -NoNewWindow -RedirectStandardError $log `
    -ArgumentList "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", "http://127.0.0.1:$puerto"

Write-Host "Abriendo el túnel..."
$direccion = $null
for ($i = 0; $i -lt 60 -and -not $direccion; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $log) {
        $encontrada = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
        if ($encontrada) { $direccion = $encontrada.Matches[0].Value }
    }
}
Write-Host ""
if ($direccion) {
    $dominio = $direccion -replace "^https://", ""
    $env:DJANGO_ALLOWED_HOSTS = "$dominio,localhost,127.0.0.1"
    try { Set-Clipboard -Value $direccion } catch { }
    Write-Host "  Dirección para la PC y el celular (ya está copiada):" -ForegroundColor Green
    Write-Host "  $direccion" -ForegroundColor Yellow
    Write-Host "  En la PC también sirve http://localhost:$puerto (pero sin modo sin señal)."
} else {
    # Sin internet (o con el túnel caído) el sistema igual se usa desde esta PC.
    Stop-Process -Id $tunel.Id -ErrorAction SilentlyContinue
    $env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1"
    Write-Host "  No se pudo abrir el túnel: los celulares no van a poder entrar." -ForegroundColor Red
    Write-Host "  Revisá la conexión a internet (o apagá la VPN) y el archivo cloudflared.log."
    Write-Host "  Desde esta PC: http://localhost:$puerto" -ForegroundColor Yellow
}
Write-Host "  Para terminar: Ctrl+C"
Write-Host ""

try {
    & $py manage.py runserver "127.0.0.1:$puerto"
}
finally {
    Stop-Process -Id $tunel.Id -ErrorAction SilentlyContinue
    Write-Host "Túnel cerrado."
}
