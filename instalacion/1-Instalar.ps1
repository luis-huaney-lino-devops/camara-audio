# Instalación de camvoice: comprueba la máquina y registra la cámara virtual.
#
# Es idempotente: se puede ejecutar varias veces sin romper nada.
# Se relanza solo como administrador, que hace falta para escribir en HKLM.
#
#   .\1-Instalar.ps1
#   .\1-Instalar.ps1 -Nombre "Mi cámara"

[CmdletBinding()]
param(
    [string]$Nombre = 'CamVoice Virtual Camera'
)

. (Join-Path $PSScriptRoot '_comun.ps1')

Write-Titulo 'camvoice - Instalacion'

# --------------------------------------------------------------------------- #
# 1. Requisitos de la máquina
# --------------------------------------------------------------------------- #

Write-Paso 1 'Comprobando la maquina'
if (-not (Test-Requisitos)) {
    Write-Host ''
    Write-Error2 'La maquina no cumple los requisitos minimos. Ver docs/REQUISITOS.md'
    Pausar
    exit 1
}

# --------------------------------------------------------------------------- #
# 2. Localizar la aplicación
# --------------------------------------------------------------------------- #

Write-Paso 2 'Localizando la aplicacion'
$app = Find-Camvoice
if ($null -eq $app) {
    Write-Error2 'No se encontro camvoice.'
    Write-Info   'Este script debe estar junto a camvoice-cli.exe, o dentro del'
    Write-Info   'arbol de codigo fuente (carpeta instalacion\).'
    Pausar
    exit 1
}
Write-Ok "Encontrada via $($app.Tipo)"
Write-Info $app.Programa

# --------------------------------------------------------------------------- #
# 3. Elevación
# --------------------------------------------------------------------------- #

Write-Paso 3 'Comprobando permisos'
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
# 4. Estado previo
# --------------------------------------------------------------------------- #

Write-Paso 4 'Camaras ya registradas en el sistema'
$previas = Get-CamarasVirtuales
if ($previas.Count -eq 0) {
    Write-Info 'Ninguna.'
} else {
    foreach ($c in $previas) { Write-Info "$($c.Nombre)  ($($c.Arq))" }
    if ($previas.Nombre -contains $Nombre) {
        Write-Aviso "«$Nombre» ya estaba registrada. Se volvera a registrar (no pasa nada)."
    }
}

# --------------------------------------------------------------------------- #
# 5. Registro
# --------------------------------------------------------------------------- #

Write-Paso 5 "Registrando la camara como «$Nombre»"
$res = Invoke-Camvoice $app @('install-camera', '--name', $Nombre)
foreach ($linea in ($res.Salida -split "`r?`n")) {
    if ($linea.Trim()) { Write-Info $linea }
}
if ($res.Codigo -ne 0) {
    Write-Error2 'El registro fallo. Ver el detalle arriba.'
    Pausar
    exit 1
}

# --------------------------------------------------------------------------- #
# 6. Verificación independiente
# --------------------------------------------------------------------------- #

Write-Paso 6 'Verificando el resultado en el registro de Windows'
# Se consulta el registro directamente, no la salida de la aplicacion: asi la
# comprobacion es independiente de lo que la aplicacion crea haber hecho.
$ahora = Get-CamarasVirtuales | Where-Object { $_.Nombre -eq $Nombre }
if ($ahora.Count -eq 0) {
    Write-Error2 "«$Nombre» no aparece en el registro de Windows."
    Pausar
    exit 1
}
foreach ($c in $ahora) { Write-Ok "$($c.Nombre)  ($($c.Arq))" }

$arquitecturas = @($ahora | ForEach-Object { $_.Arq } | Sort-Object -Unique)
if ($arquitecturas.Count -lt 2) {
    Write-Aviso 'Solo se registro una arquitectura.'
    Write-Info  'Una aplicacion de videollamada de 32 bits no vera la camara:'
    Write-Info  'un filtro DirectShow solo se carga en procesos de su misma arquitectura.'
}

# --------------------------------------------------------------------------- #
# 7. Diagnóstico completo
# --------------------------------------------------------------------------- #

Write-Paso 7 'Diagnostico de la aplicacion'
$diag = Invoke-Camvoice $app @('doctor')
foreach ($linea in ($diag.Salida -split "`r?`n")) {
    if (-not $linea.Trim()) { continue }
    if     ($linea -match '\[--\]') { Write-Host "    $linea" -ForegroundColor Red }
    elseif ($linea -match '\[!!\]') { Write-Host "    $linea" -ForegroundColor Yellow }
    elseif ($linea -match '\[ok\]') { Write-Host "    $linea" -ForegroundColor Green }
    else                            { Write-Info $linea }
}

# --------------------------------------------------------------------------- #
# 8. Resumen
# --------------------------------------------------------------------------- #

Write-Titulo 'Instalacion terminada'

if ($diag.Codigo -ne 0) {
    Write-Aviso 'El diagnostico reporto avisos. La camara esta registrada, pero'
    Write-Aviso 'falta configurar algo (normalmente, seleccionar los dos clips).'
    Write-Host ''
}

Write-Host '  Siguiente paso: prueba rapida sin grabar nada' -ForegroundColor White
Write-Host '      .\2-ProbarRapido.ps1' -ForegroundColor Cyan
Write-Host ''
Write-Host '  O abre la aplicacion directamente:' -ForegroundColor White
if ($app.Gui -and (Test-Path $app.Gui)) {
    Write-Host "      $($app.Gui)" -ForegroundColor Cyan
} else {
    Write-Host '      python -m camvoice' -ForegroundColor Cyan
}
Write-Host ''
Write-Host "  En Teams, Zoom o Chrome elige la camara: «$Nombre»" -ForegroundColor White
Write-Host '  Importante: cierralos y vuelve a abrirlos. Enumeran las camaras' -ForegroundColor DarkGray
Write-Host '  al arrancar y no se enteran de una nueva a mitad de sesion.' -ForegroundColor DarkGray

Pausar
exit 0
