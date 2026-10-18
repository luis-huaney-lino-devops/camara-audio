# La cámara virtual: independiente de OBS

**Resuelto. OBS ya no hace falta.** La aplicación registra su propio filtro y
aparece como **«CamVoice Virtual Camera»** en Teams, Zoom, Meet y el navegador.

Verificado en este equipo el 2026-10-17:

```
camvoice-cli install-camera
  [ok] Filtro registrado como «CamVoice Virtual Camera»:
       UnityCaptureFilter32.dll, UnityCaptureFilter64.dll

camvoice-cli cameras
  [ok] CamVoice Virtual Camera (64-bit)  backend=unitycapture
  [ok] CamVoice Virtual Camera (32-bit)  backend=unitycapture
```

Y alimentándola de verdad, 60 s: **15,03 FPS, 9,4 % de un núcleo, memoria
estable**. OBS Studio no intervino.

---

## 1. Por qué hace falta registrar algo

Cuando eliges una cámara en Teams, Teams **no habla con otro programa**. Carga
una DLL registrada en el sistema **dentro de su propio proceso** y le pide
fotogramas.

```
  ┌──────────────────────────┐        ┌───────────────────────────────┐
  │  Proceso de Teams        │        │  Proceso de camvoice          │
  │                          │        │                               │
  │  ┌────────────────────┐  │        │  ┌─────────────────────────┐  │
  │  │ el filtro .dll     │◄─┼────────┼──┤ motor de render         │  │
  │  │ (cargado por Teams)│  │ memoria│  │ (Python)                │  │
  │  └────────────────────┘  │compart.│  └─────────────────────────┘  │
  └──────────────────────────┘        └───────────────────────────────┘
          la "cámara"                        quien la alimenta
```

De ahí se sigue algo que no se puede sortear:

> **Ninguna aplicación en Python puede ser, por sí misma, una cámara de
> Windows.** Hace falta una DLL nativa registrada en el sistema.

Lo que sí puede hacer Python es **alimentarla** por memoria compartida. Por eso
la pregunta nunca fue «¿con DLL o sin DLL?», sino **qué DLL y quién la
registra**. La respuesta ahora es: la nuestra, y la registra nuestro instalador.

### Por qué DirectShow y no Media Foundation

| | DirectShow (elegido) | Media Foundation |
|---|---|---|
| Estado | Antiguo, pero universal en la práctica | Moderno, Windows 11 22H2+ |
| Lo ven | Teams, Zoom, Meet, Discord, Chrome, Edge, OBS | Lo anterior **más** la app Cámara de Windows |
| Firma de código | **No hace falta** | Necesaria, y en la práctica empaquetado MSIX |
| Instalación | `regsvr32`, una vez, con permisos de administrador | Paquete firmado |

DirectShow cubre todas las aplicaciones de videollamada que importan y no exige
comprar un certificado de firma. La única pérdida es la app **Cámara de
Windows**, que no sirve de todos modos para comprobar nada útil.

---

## 2. Qué se usa y por qué

El filtro es **UnityCaptureFilter**, de
[github.com/schellingb/UnityCapture](https://github.com/schellingb/UnityCapture),
con **licencia MIT**. Pese al nombre, **no requiere ni Unity ni OBS**: es un
filtro DirectShow autónomo de propósito general.

Se incluyen las dos arquitecturas en `vendor/filters/`:

| Archivo | Arquitectura | Tamaño |
|---|---|---|
| `UnityCaptureFilter64.dll` | x64 | 157.696 bytes |
| `UnityCaptureFilter32.dll` | x86 | 168.448 bytes |

**Las dos hacen falta.** Un filtro DirectShow solo se carga en procesos de su
misma arquitectura: una aplicación de videollamada de 32 bits **únicamente**
verá el filtro de 32 bits.

### El detalle que lo cambió todo: el nombre es un parámetro

El plan inicial contemplaba compilar un filtro propio solo para que la cámara no
se llamara «Unity Video Capture». Resultó innecesario: el filtro acepta el
nombre **como parámetro de registro**.

```
regsvr32 /s "UnityCaptureFilter64.dll" "/i:UnityCaptureName=CamVoice Virtual Camera"
```

Dos detalles de esa línea no son opcionales, y ambos tienen prueba automatizada
en `tests/test_filters.py`:

- **El token `/i:` va entre comillas.** El nombre lleva espacios; sin comillas
  `regsvr32` lo corta por el primero y la cámara se llamaría «CamVoice».
- **No se usa `/n`.** Esa bandera saltaría `DllRegisterServer` y el filtro
  quedaría a medio registrar: no aparecería como cámara.

Eso elimina la única razón de peso para mantener un filtro compilado a medida, y
con ella la necesidad de un compilador de C++.

---

## 3. Uso

```bash
camvoice-cli install-camera                 # registra como «CamVoice Virtual Camera»
camvoice-cli install-camera --name "Mi cámara"   # con otro nombre
camvoice-cli cameras                        # comprueba qué hay registrado
camvoice-cli install-camera --uninstall     # quita el registro
```

`install-camera` pide elevación por UAC, porque escribe en `HKLM`. Es **una sola
vez**: luego la cámara queda registrada en el sistema y la aplicación la usa sin
permisos especiales.

Después de registrar, **cierra y vuelve a abrir** Teams, Zoom o el navegador:
enumeran las cámaras al arrancar y no se enteran de una nueva a mitad de sesión.

### Selección automática

`output.backend` vale `"auto"` por defecto. La aplicación consulta el registro
de Windows y **prefiere el filtro que no depende de OBS**:

```
backend elegido por auto: ['unitycapture', 'obs']
```

Con eso, en un equipo que tenga los dos registrados usa el propio y no compite
con OBS por el dispositivo. Si solo hay OBS, lo usa y funciona igual.

---

## 4. Lo que está implementado

En `camvoice/output/filters.py` y `camvoice/output/sink.py`:

- **Enumeración real del registro de Windows**, en las vistas de 64 y 32 bits,
  con CLSID, ruta de la DLL y si esa DLL todavía existe (detecta registros
  huérfanos de desinstalaciones mal hechas).
- **Selección automática de backend** que prefiere el filtro independiente.
- **Registro y desregistro** con elevación por UAC y nombre configurable.
- **Aviso si falta una de las dos arquitecturas.**
- **Mensajes de error que dicen qué hacer**, distinguiendo «no hay filtro» de
  «el filtro está ocupado por otra aplicación».
- **20 pruebas** en `tests/test_filters.py`, que no modifican el sistema.

El `.spec` de PyInstaller empaqueta `vendor/filters/` en la subcarpeta
`filters/` del ejecutable, donde `filters_dir()` la busca con `sys._MEIPASS`.

---

## 5. Rendimiento con este filtro

El formato nativo de UnityCapture es **RGBA**, no NV12, así que hay una
conversión por fotograma que con el filtro de OBS no existía. Medido:

| Formato interno | Coste de conversión |
|---|---|
| NV12 → RGBA | 0,003 ms por fotograma |
| BGR → RGBA | 0,004 ms por fotograma |

Frente a los 66,7 ms de presupuesto a 15 FPS, la diferencia es irrelevante, así
que **se mantiene NV12**: ocupa la mitad en la caché de fotogramas y conserva la
ventaja con el backend de OBS.

Comparativa de 60 s con los dos filtros, mismo equipo y configuración:

| | Filtro de OBS | CamVoice (UnityCapture) |
|---|---|---|
| FPS efectivos | 15,02 | 15,03 |
| CPU (% de un núcleo) | 10,6 % | 9,4 % |
| Coste por fotograma (p95) | 2,05 ms | 5,03 ms |
| Memoria | estable | estable |

Los 3 ms adicionales de p95 son el coste real de la conversión a RGBA bajo
carga. Sigue siendo el 7,5 % del presupuesto.

---

## 6. Licencias

`UnityCaptureFilter` es **MIT**: se puede redistribuir sin restricciones, en
producto abierto o cerrado. Ver `docs/LICENCIAS.md` §3.

Nótese que esto **mejora** la situación de licencia del proyecto: antes la ruta
por defecto dependía de OBS Studio (GPL-2.0). La dependencia que queda bajo
GPL-2.0 es `pyvirtualcam`, y `output/sink.py` la aísla detrás del protocolo
`FrameSink` por si hubiera que sustituirla.

---

## 7. Si algún día hace falta un filtro compilado a medida

Ya no es necesario para el nombre, pero seguiría siendo la vía para:

- **CLSID propio**, de modo que no colisione con una instalación de UnityCapture
  hecha por otro programa.
- **Formato nativo NV12**, que quitaría la conversión por fotograma.
- **Varios dispositivos** simultáneos.

Haría falta instalar en Visual Studio 2022 el workload «Desarrollo de escritorio
con C++» (comprobado: está Visual Studio pero sin compilador ni SDK), partir del
código MIT de UnityCapture y compilar en x64 y x86. El código Python no
cambiaría: ya detecta cualquier filtro registrado.

**No lo recomiendo salvo que aparezca una de esas tres necesidades.**
