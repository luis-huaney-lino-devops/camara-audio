# Deja la máquina como estaba: quita el registro de la cámara y, si se pide,
# los datos de la aplicación.
#
#   .\9-Desinstalar.ps1
#   .\9-Desinstalar.ps1 -BorrarDatos     # tambien configuracion y cache
#
# Se relanza solo como administrador.
#
# IMPORTANTE: ejecutarlo ANTES de borrar la carpeta del programa. Si se borra
# primero, el registro queda huerfano apuntando a una DLL que ya no existe, y
# haria falta volver a descomprimir para poder limpiarlo.

[CmdletBinding()]
param(
    [switch]$BorrarDatos
)

. (Join-Path $PSScriptRoot '_comun.ps1')

Write-Titulo 'camvoice - Desinstalacion'

# --------------------------------------------------------------------------- #

Write-Paso 1 'Localizando la aplicacion'
$app = Find-Camvoice
if ($null -eq $app) {
    Write-Error2 'No se encontro camvoice junto a estos scripts.'
    Write-Info   'Si ya borraste la carpeta del programa, el registro de la camara'
    Write-Info   'sigue puesto. Vuelve a descomprimir y ejecuta este script.'
    Pausar
    exit 1
}
Write-Ok "Encontrada via $($app.Tipo)"

# --------------------------------------------------------------------------- #

Write-Paso 2 'Comprobando permisos'
if (-not (Test-Administrador)) {
    if (Request-Elevacion $PSCommandPath) {
        Write-Info 'Continua en la ventana de administrador. Esta se puede cerrar.'
        Pausar
        exit 0
    }
    Pausar
    exit 1
}
Write-Ok 'Ejecutando como administrador'

# --------------------------------------------------------------------------- #

Write-Paso 3 'Cerrando la aplicacion si esta abierta'
$abiertos = @(Get-Process -Name 'camvoice', 'camvoice-cli' -ErrorAction SilentlyContinue)
if ($abiertos.Count -eq 0) {
    Write-Ok 'No estaba abierta'
} else {
    foreach ($p in $abiertos) {
        try {
            Stop-Process -Id $p.Id -Force
            Write-Ok "Cerrado $($p.ProcessName) (PID $($p.Id))"
        } catch {
            Write-Aviso "No se pudo cerrar $($p.ProcessName) (PID $($p.Id))"
        }
    }
    Start-Sleep -Seconds 1
}

# --------------------------------------------------------------------------- #

Write-Paso 4 'Quitando el registro de la camara'
$antes = @(Get-CamarasVirtuales | Where-Object { $_.Nombre -match 'CamVoice|Unity Video Capture' })
if ($antes.Count -eq 0) {
    Write-Info 'No habia ninguna camara propia registrada.'
} else {
    foreach ($c in $antes) { Write-Info "Quitando: $($c.Nombre)  ($($c.Arq))" }
    $res = Invoke-Camvoice $app @('install-camera', '--uninstall')
    foreach ($linea in ($res.Salida -split "`r?`n")) {
        if ($linea.Trim()) { Write-Info $linea }
    }
}

# --------------------------------------------------------------------------- #

Write-Paso 5 'Verificando'
# Se consulta el registro directamente, no lo que la aplicacion diga.
$despues = @(Get-CamarasVirtuales | Where-Object { $_.Nombre -match 'CamVoice|Unity Video Capture' })
if ($despues.Count -eq 0) {
    Write-Ok 'La camara ya no esta registrada en el sistema'
} else {
    Write-Error2 'Siguen registradas:'
    foreach ($c in $despues) { Write-Info "$($c.Nombre)  ($($c.Arq))" }
}

# --------------------------------------------------------------------------- #

Write-Paso 6 'Datos de la aplicacion'
$datos = Join-Path $env:APPDATA 'camvoice'
if (-not (Test-Path $datos)) {
    Write-Info 'No hay datos que borrar.'
} elseif ($BorrarDatos) {
    $tam = (Get-ChildItem $datos -Recurse -File -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
    try {
        Remove-Item $datos -Recurse -Force
        Write-Ok ("Borrados {0:N0} MB de {1}" -f ($tam / 1MB), $datos)
    } catch {
        Write-Error2 "No se pudo borrar $datos : $_"
    }
} else {
    $tam = (Get-ChildItem $datos -Recurse -File -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
    Write-Info ("Se conservan {0:N0} MB en {1}" -f ($tam / 1MB), $datos)
    Write-Info 'Incluye la configuracion y la cache de fotogramas.'
    Write-Info 'Para borrarlos tambien:  .\9-Desinstalar.ps1 -BorrarDatos'
}

# --------------------------------------------------------------------------- #

Write-Titulo 'Desinstalacion terminada'
Write-Host '  Ya puedes borrar la carpeta del programa.' -ForegroundColor White
if (-not $BorrarDatos -and (Test-Path $datos)) {
    Write-Host "  Quedan los datos en: $datos" -ForegroundColor DarkGray
}

Pausar
exit 0
