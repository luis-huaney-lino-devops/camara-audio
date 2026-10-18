# Funciones compartidas por los scripts de instalación.
# No se ejecuta directamente: lo cargan los demás con dot-sourcing.

$ErrorActionPreference = 'Stop'

# La aplicación escribe en UTF-8. Sin esto, PowerShell decodifica su salida con
# la página de códigos ANSI del sistema y los acentos aparecen como «Micr?fono».
# Afecta solo a esta sesión; no cambia la configuración del equipo.
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch {
    # Consolas sin soporte (redirigidas, algunos hosts): se sigue sin ello.
}

# --------------------------------------------------------------------------- #
# Presentación
# --------------------------------------------------------------------------- #

function Write-Titulo($texto) {
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
    Write-Host "  $texto" -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
    Write-Host ''
}

function Write-Paso($numero, $texto) {
    Write-Host ''
    Write-Host "[$numero] $texto" -ForegroundColor White
}

function Write-Ok($texto)     { Write-Host "    [ok] $texto" -ForegroundColor Green }
function Write-Aviso($texto)  { Write-Host "    [!!] $texto" -ForegroundColor Yellow }
function Write-Error2($texto) { Write-Host "    [--] $texto" -ForegroundColor Red }
function Write-Info($texto)   { Write-Host "         $texto" -ForegroundColor DarkGray }

function Pausar {
    # Solo tiene sentido si hay una consola interactiva: al relanzar elevado la
    # ventana se cierra sola al terminar y no daría tiempo a leer nada.
    if ($Host.Name -eq 'ConsoleHost' -and -not [Console]::IsInputRedirected) {
        Write-Host ''
        Write-Host 'Pulsa una tecla para cerrar...' -ForegroundColor DarkGray
        $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
    }
}

# --------------------------------------------------------------------------- #
# Permisos
# --------------------------------------------------------------------------- #

function Test-Administrador {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Request-Elevacion($rutaScript) {
    <#
      Se relanza a sí mismo como administrador.

      Hace falta porque registrar un filtro DirectShow escribe en HKLM. Se
      relanza con -NoExit para que la ventana elevada no desaparezca antes de
      que se pueda leer el resultado, que es el fallo clásico de este patrón.
    #>
    Write-Aviso 'Hacen falta permisos de administrador para registrar la cámara.'
    Write-Info  'Se abrirá una ventana nueva. Acepta el aviso de Windows.'
    Write-Host ''
    $argumentos = @(
        '-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', "`"$rutaScript`""
    )
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $argumentos -Verb RunAs | Out-Null
    } catch {
        Write-Error2 'Se rechazó la elevación o no se pudo abrir la ventana.'
        Write-Info   'Alternativa: pulsa botón derecho sobre este script y elige'
        Write-Info   '"Ejecutar con PowerShell" desde una sesión de administrador.'
        return $false
    }
    return $true
}

# --------------------------------------------------------------------------- #
# Localización de la aplicación
# --------------------------------------------------------------------------- #

function Find-Camvoice {
    <#
      Devuelve un objeto con cómo invocar la aplicación.

      Funciona en los dos escenarios sin que el usuario tenga que elegir:
        - paquete distribuido: camvoice-cli.exe junto a los scripts o un nivel
          por encima;
        - árbol de código fuente: se usa el entorno virtual .venv, y si no, el
          Python del sistema.
    #>
    $raiz = Split-Path -Parent $PSScriptRoot

    $candidatosExe = @(
        (Join-Path $PSScriptRoot 'camvoice-cli.exe'),
        (Join-Path $raiz 'camvoice-cli.exe'),
        (Join-Path $raiz 'dist\camvoice\camvoice-cli.exe')
    )
    foreach ($exe in $candidatosExe) {
        if (Test-Path $exe) {
            return [pscustomobject]@{
                Tipo     = 'ejecutable'
                Programa = $exe
                Base     = @()
                Gui      = (Join-Path (Split-Path -Parent $exe) 'camvoice.exe')
                Raiz     = Split-Path -Parent $exe
            }
        }
    }

    $candidatosPython = @(
        (Join-Path $raiz '.venv\Scripts\python.exe'),
        'python.exe'
    )
    foreach ($py in $candidatosPython) {
        $existe = (Test-Path $py) -or ($null -ne (Get-Command $py -ErrorAction SilentlyContinue))
        if ($existe -and (Test-Path (Join-Path $raiz 'camvoice\__main__.py'))) {
            return [pscustomobject]@{
                Tipo     = 'codigo fuente'
                Programa = $py
                Base     = @('-m', 'camvoice')
                Gui      = $null
                Raiz     = $raiz
            }
        }
    }
    return $null
}

function Invoke-Camvoice($app, [string[]]$argumentos) {
    <# Ejecuta un subcomando y devuelve la salida junto al código de salida. #>
    Push-Location $app.Raiz
    try {
        $todos = @($app.Base) + $argumentos
        $salida = & $app.Programa @todos 2>&1 | Out-String
        return [pscustomobject]@{ Salida = $salida; Codigo = $LASTEXITCODE }
    } finally {
        Pop-Location
    }
}

# --------------------------------------------------------------------------- #
# Comprobaciones del sistema
# --------------------------------------------------------------------------- #

function Test-Requisitos {
    <# Devuelve $true si la máquina puede ejecutar la aplicación. #>
    $ok = $true

    $os = Get-CimInstance Win32_OperatingSystem
    $version = [Version]$os.Version
    if ($version.Major -ge 10) {
        Write-Ok "Windows $($os.Caption.Trim()) (build $($version.Build))"
    } else {
        Write-Error2 "Hace falta Windows 10 u 11. Detectado: $($os.Caption)"
        $ok = $false
    }

    if ([Environment]::Is64BitOperatingSystem) {
        Write-Ok 'Windows de 64 bits'
    } else {
        Write-Error2 'Hace falta un Windows de 64 bits.'
        $ok = $false
    }

    $ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    if ($ram -ge 7.5) { Write-Ok "$ram GB de RAM" }
    else { Write-Aviso "$ram GB de RAM. Recomendado 8 GB o más." }

    $unidad = (Get-Item $PSScriptRoot).PSDrive.Name
    $libre = [math]::Round((Get-PSDrive $unidad).Free / 1GB, 1)
    if ($libre -ge 1.5) {
        Write-Ok "$libre GB libres en $unidad`:"
    } else {
        Write-Aviso "Solo $libre GB libres en $unidad`:. La caché de fotogramas necesita espacio."
        Write-Info  'Unos 7 MB por segundo de clip a 640x480 y 15 FPS.'
    }

    $micros = @(Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
                Where-Object { $_.PNPClass -in @('AudioEndpoint', 'Media') -and $_.Status -eq 'OK' })
    if ($micros.Count -gt 0) {
        Write-Ok "$($micros.Count) dispositivos de audio en el sistema"
    } else {
        Write-Aviso 'No se detectaron dispositivos de audio por WMI.'
        Write-Info  'El diagnóstico de la aplicación lo confirmará más adelante.'
    }

    return $ok
}

function Get-CamarasVirtuales {
    <# Filtros DirectShow registrados, de las dos arquitecturas. #>
    $clave = 'CLSID\{860BB310-5D01-11D0-BD3B-00A0C911CE86}\Instance'
    $vistas = @(
        @{ Ruta = "HKLM:\SOFTWARE\Classes\$clave";              Arq = '64-bit' },
        @{ Ruta = "HKLM:\SOFTWARE\Classes\WOW6432Node\$clave";  Arq = '32-bit' }
    )
    $resultado = @()
    foreach ($vista in $vistas) {
        if (-not (Test-Path $vista.Ruta)) { continue }
        Get-ChildItem $vista.Ruta -ErrorAction SilentlyContinue | ForEach-Object {
            $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
            if ($props.FriendlyName) {
                $resultado += [pscustomobject]@{
                    Nombre = $props.FriendlyName
                    Arq    = $vista.Arq
                }
            }
        }
    }
    return $resultado
}
