# camvoice

Cámara virtual para Windows que reproduce un clip pregrabado tuyo **hablando**
cuando detecta tu voz, y otro **en silencio** cuando callas.

Sin IA generativa, sin servicios externos, sin GPU y sin conexión a Internet.
Todo se procesa en local: el audio se analiza trama a trama para decidir entre
dos estados y se descarta; no se graba ni se envía a ninguna parte.

```
   micrófono ──► VAD ──► histéresis ──► ¿hablas? ──► clip correspondiente ──► cámara virtual
```

## Empezar

### Solo quiero probarlo

Descarga **[camvoice-win64.zip](https://github.com/luis-huaney-lino-devops/camara-audio/releases/latest)**
(96 MB, Windows x64, autocontenido), descomprime y en `instalacion\`:

```
INSTALAR.bat     -> acepta el aviso de Windows (UAC), una sola vez
PROBAR.bat       -> abre el navegador y verás la imagen moverse con tu voz
```

No necesita OBS, ni Python, ni drivers, ni Internet. Requisitos completos en
[docs/REQUISITOS.md](docs/REQUISITOS.md).

### Desde el código

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python -m camvoice doctor                  # comprueba el entorno
python -m camvoice make-clips --outdir clips   # clips de prueba, sin grabar nada
python -m camvoice                         # interfaz gráfica
```

Para usarlo en una videollamada, registra la cámara **una sola vez**:

```bash
python -m camvoice install-camera      # acepta el aviso de UAC
```

En una máquina nueva es más rápido con los scripts de `instalacion\`: doble
clic en `INSTALAR.bat` y luego en `PROBAR.bat`.

Aparece como **«CamVoice Virtual Camera»** en Teams, Zoom, Meet y el navegador.
**No hace falta OBS.** Detalles en [docs/CAMARA-VIRTUAL.md](docs/CAMARA-VIRTUAL.md).

## Comandos

| Comando | Para qué |
|---|---|
| `camvoice` | Interfaz gráfica (acción por defecto) |
| `camvoice doctor` | Comprueba entorno, audio, cámara virtual y configuración |
| `camvoice devices` | Lista los micrófonos |
| `camvoice cameras` | Lista los filtros de cámara registrados en Windows |
| `camvoice install-camera` | Registra la cámara «CamVoice Virtual Camera» (sin OBS) |
| `camvoice probe video.mp4` | Propiedades de un clip |
| `camvoice prepare` | Construye la caché de fotogramas |
| `camvoice run` | Ejecuta sin interfaz |
| `camvoice vad-test` | Ajusta la sensibilidad del detector con el micrófono real |
| `camvoice bench --duration 1800` | Mide CPU, memoria y FPS |
| `camvoice make-clips` | Genera clips sintéticos de prueba |
| `camvoice clean` | Vacía la caché de fotogramas |

Con el ejecutable empaquetado, los subcomandos van en `camvoice-cli.exe` y la
interfaz en `camvoice.exe`.

## Generar el ejecutable

El ejecutable **no se versiona**: son 227 MB de contenido derivado. Se compila
en poco mas de un minuto:

```bash
pip install -r requirements-dev.txt
pyinstaller build/camvoice.spec --noconfirm
```

Queda en `dist/camvoice/`, autocontenido: sin Python, sin OBS y sin el Visual
C++ Redistributable. Detalles y comprobaciones en
[docs/INSTALACION.md](docs/INSTALACION.md) seccion 2.

## Cómo funciona

Dos decisiones explican el rendimiento y la estabilidad:

**Los clips se decodifican una sola vez.** Cada clip se convierte a un archivo
de fotogramas en crudo, ya en la resolución, los FPS y el formato de píxel de
salida. En ejecución, obtener un fotograma es indexar un `memmap`: una copia de
memoria, sin decodificador en el bucle. El formato interno es NV12, que además
ocupa la mitad que BGR y permite calcular el fundido cruzado sobre los bytes
crudos sin error, porque YUV es una transformación afín de RGB.

**La decisión de "hablas o no" está separada del hardware.** `SpeechGate` y
`FrameComposer` son deterministas y no tocan audio, reloj ni cámara: reciben un
booleano o un estado y devuelven el siguiente estado o fotograma. Por eso todo
el comportamiento se verifica sin micrófono ni cámara, en 15 segundos.

Detalles en [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

## Rendimiento medido

En un Intel de 4 núcleos a 2,5 GHz, 640 × 480 a 15 FPS, alimentando la cámara
virtual de verdad:

| | CamVoice (propia) | Filtro de OBS |
|---|---|---|
| FPS efectivos | 15,03 | 15,02 |
| CPU | 9,4 % de un núcleo = 1,2 % de la máquina | 10,6 % = 1,3 % |
| Memoria | estable | estable |
| Coste por fotograma (p95) | 5,03 ms | 2,05 ms |

Los 66,7 ms de presupuesto por fotograma a 15 FPS quedan usados al 7,5 % en el
peor de los dos casos.

Cifras completas y metodología en [docs/METRICS.md](docs/METRICS.md). Falta
repetirlo en el Core i5 de referencia y durante 30 minutos.

## Pruebas

```bash
pytest          # 165 pruebas, ~15 s, sin micrófono ni cámara
```

Procedimiento completo, incluidas las pruebas manuales, en
[docs/PRUEBAS.md](docs/PRUEBAS.md).

## Lo que esto NO hace

No sincroniza los labios. El clip de habla se reproduce cuando se detecta voz,
sin relación con las palabras: habrá desajuste entre el audio y la boca, por
construcción. El primer fonema nunca está sincronizado. Risas, toses y
carraspeos activan el estado de habla. **Quien se fije lo notará.**

El objetivo es estabilidad y bajo consumo, no una falsa promesa de
sincronización. Límites completos en [plan.md](plan.md) §5.

## Documentación

| | |
|---|---|
| [plan.md](plan.md) | Especificación, criterios de aceptación y estado |
| [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) | Diseño y decisiones técnicas |
| [docs/REQUISITOS.md](docs/REQUISITOS.md) | **Qué hace falta para probarlo en una máquina nueva** |
| [instalacion/00-LEEME.md](instalacion/00-LEEME.md) | **Scripts de instalación rápida y orden de ejecución** |
| [docs/CAMARA-VIRTUAL.md](docs/CAMARA-VIRTUAL.md) | La cámara virtual, independiente de OBS |
| [docs/INSTALACION.md](docs/INSTALACION.md) | Instalación, configuración, desinstalación |
| [docs/PRUEBAS.md](docs/PRUEBAS.md) | Procedimiento de pruebas |
| [docs/METRICS.md](docs/METRICS.md) | Mediciones de rendimiento |
| [docs/LICENCIAS.md](docs/LICENCIAS.md) | Dependencias y licencias |
