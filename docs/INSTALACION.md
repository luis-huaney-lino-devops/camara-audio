# Instalación, configuración y desinstalación

## 1. Registrar la cámara virtual

La aplicación trae su propio filtro de cámara. **No hace falta OBS Studio.**

Tras descomprimir, abre una consola en la carpeta y ejecuta **una sola vez**:

```
camvoice-cli install-camera
```

Acepta el aviso de Control de cuentas de usuario (hace falta porque se escribe
en el registro del sistema). Debe responder:

```
[ok] Filtro registrado como «CamVoice Virtual Camera»:
     UnityCaptureFilter32.dll, UnityCaptureFilter64.dll
```

Comprobar que quedó bien:

```
camvoice-cli cameras
```

Para ponerle otro nombre: `camvoice-cli install-camera --name "Mi cámara"`.
Para quitarla: `camvoice-cli install-camera --uninstall`.

### Lo que conviene saber de este mecanismo

- **Es un filtro DirectShow.** Teams, Zoom, Google Meet, Discord, Slack, OBS y
  cualquier aplicación basada en Chromium lo ven. La app **Cámara de Windows**
  no, porque usa solo Media Foundation: no sirve para comprobar que funciona.
- **Se registran dos DLL, de 32 y 64 bits.** Un filtro solo se carga en
  procesos de su misma arquitectura, y hay aplicaciones de videollamada de
  32 bits.
- **Las aplicaciones enumeran las cámaras al arrancar.** Después de registrar,
  cierra y vuelve a abrir Teams o el navegador para que aparezca.
- **Un solo productor a la vez** por filtro. Si abres dos copias de camvoice, la
  segunda no podrá tomarlo.

### Alternativa: usar el filtro de OBS

Si ya tienes OBS Studio instalado, su filtro también sirve y la aplicación lo
detecta solo (**sin abrir OBS**). `camvoice-cli cameras` muestra cuál se usará;
con la configuración por defecto se prefiere el propio.

## 2. Dónde está el ejecutable

**El ejecutable no se versiona en el repositorio.** Son 227 MB de contenido
derivado (Qt, OpenCV, NumPy y el intérprete de Python empaquetados), y un
repositorio de código no es el sitio para eso. Hay tres formas de conseguirlo.

### 2.0. Descargarlo ya compilado (lo más rápido)

**<https://github.com/luis-huaney-lino-devops/camara-audio/releases/latest>**

Archivo: `camvoice-win64.zip`, 96 MB. Lleva dentro los dos ejecutables, los
scripts de `instalacion\` y el filtro de cámara. Descomprime y ve al §2.3.

### 2.1. Si ya lo tienes compilado

Queda en la carpeta `dist` de la raíz del proyecto:

```
dist\camvoice\camvoice.exe          <- interfaz grafica (doble clic)
dist\camvoice\camvoice-cli.exe      <- subcomandos de consola
dist\camvoice\instalacion\          <- scripts de instalacion rapida
dist\camvoice-win64.zip             <- el paquete comprimido, ~92 MB
```

Para llevarlo a otra máquina, copia **`dist\camvoice-win64.zip`** o la carpeta
`dist\camvoice\` entera. Es autocontenida: no necesita Python, ni OBS, ni el
Visual C++ Redistributable.

### 2.2. Si no lo tienes: compilarlo

Desde la raíz del proyecto, con el entorno virtual preparado (§3):

```powershell
.venv\Scripts\activate
pip install -r requirements-dev.txt
pyinstaller build\camvoice.spec --noconfirm
```

Tarda poco más de un minuto y deja el resultado en `dist\camvoice\`. Para
generar también el ZIP distribuible:

```powershell
Compress-Archive -Path dist\camvoice\* -DestinationPath dist\camvoice-win64.zip -Force
```

Comprobación de que la compilación salió bien, **que conviene hacer siempre**
porque un `.spec` mal configurado produce un ejecutable que compila sin errores
y falla al arrancar:

```powershell
dir dist\camvoice\_internal\PySide6      # Qt tiene que estar
dir dist\camvoice\_internal\filters      # las DLL del filtro de camara
dist\camvoice\camvoice-cli.exe doctor    # debe terminar con "Todo listo."
```

### 2.3. Usarlo

1. Descomprime o copia la carpeta donde quieras, por ejemplo
   `C:\Program Files\camvoice\` o una carpeta de usuario.
2. Doble clic en **`camvoice.exe`** para la interfaz gráfica.
3. Para los comandos de diagnóstico, usa **`camvoice-cli.exe`** desde una
   consola:

   ```
   camvoice-cli doctor
   camvoice-cli devices
   camvoice-cli vad-test --duration 30
   ```

Son dos ejecutables de la misma aplicación: `camvoice.exe` está construido en
modo ventana (no abre consola al hacer doble clic) y `camvoice-cli.exe` en modo
consola (necesario para que los subcomandos puedan escribir en pantalla).

No hay instalador MSI y no se instalan servicios. Lo único que se escribe fuera
de la carpeta es el registro del filtro de cámara (paso 1), que se deshace con
`camvoice-cli install-camera --uninstall`.

## 3. Instalación desde el código fuente

Requiere Python 3.11 o superior de 64 bits.

```
cd .camara-dep
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m camvoice doctor
python -m camvoice            # interfaz gráfica
```

Para desarrollo y empaquetado:

```
pip install -r requirements-dev.txt
pytest                                      # 163 pruebas, ~15 s
pyinstaller build/camvoice.spec --noconfirm
```

## 4. Primera configuración

### 4.1. Grabar los dos clips

Esto determina la calidad del resultado más que cualquier ajuste del programa.

Graba **los dos seguidos, sin mover la cámara, sin cambiar la luz y sin
levantarte**. Con la cámara del portátil y la aplicación Cámara de Windows (o la
grabación de Teams) es suficiente.

| | |
|---|---|
| **Clip «hablando»** | 8-20 s hablando con normalidad. Lee cualquier cosa en voz alta; el contenido es irrelevante |
| **Clip «silencio»** | 8-20 s callado, boca cerrada, mirando al objetivo, con tu postura de escuchar normal |
| **Primer fotograma de ambos** | Boca cerrada, mirada al frente. Es la pose desde la que se reanuda el habla tras un silencio largo |
| **Qué evitar** | Gestos amplios, manos en plano, girar la cabeza, cambiar de postura, beber agua |

Formato: MP4 con H.264 o AVI con MJPEG son las apuestas seguras. Si un video no
abre, `camvoice-cli probe video.mp4` lo dice.

Para probar la cadena completa **sin grabar nada**:

```
camvoice-cli make-clips --outdir clips
```

Genera dos clips sintéticos con una cara esquemática cuya boca se mueve.

### 4.2. Configurar

1. Abre `camvoice.exe`.
2. **1. Videos de referencia**: selecciona los dos clips. Debajo aparecen su
   resolución, cadencia y duración; si algo no cuadra, se avisa.
3. **2. Micrófono**: elige el tuyo. Los de WASAPI salen primero y son los
   preferibles.
4. **4. Salida**: deja 640 × 480 a 15 FPS para empezar. Marca «Enviar a la
   cámara virtual».
5. Pulsa **Iniciar**. La primera vez tarda unos segundos preparando la caché de
   fotogramas (hay barra de progreso).
6. Habla. El indicador de arriba debe pasar a **Voz detectada** y el video de la
   vista previa debe empezar a hablar.
7. **Guardar configuración** para no repetirlo.

### 4.3. Ajustar la sensibilidad

Si el detector se activa solo, o no se activa al hablar, mide antes de tocar:

```
camvoice-cli vad-test --duration 30
```

Muestra el nivel real, el piso de ruido, la relación señal/ruido y la cronología
de cambios. Al final informa la **tasa de cambios por minuto**: en silencio
debería ser 0.

| Síntoma | Ajuste |
|---|---|
| Se activa sin hablar | Sube **Exigencia** a 3; sube el **Umbral de voz**; sube `gate_db` (p. ej. a −45) |
| No se activa al hablar | Baja el **Umbral de voz**; baja **Exigencia**; baja `gate_db` (p. ej. a −60); acércate al micrófono |
| Tarda en arrancar | Baja **Tiempo de activación** (a costa de más falsos positivos) |
| Se corta entre palabras | Sube **Permanencia en voz** y **Tolerancia a pausas** |
| Parpadea en los dos sentidos | Sube **Permanencia en voz**; sube **Exigencia** |

**Nota sobre las mejoras de audio de Windows.** Si los niveles alternan entre
valores normales y exactamente −120 dBFS (silencio digital), es que algo en la
cadena de captura está recortando a cero: la supresión de ruido de Windows, un
«modo comunicaciones», o el software del propio micrófono. Eso degrada cualquier
VAD. Desactívalo en *Configuración > Sistema > Sonido > Entrada > tu micrófono >
Mejoras de audio*.

## 5. Uso en una videollamada

1. Arranca `camvoice.exe` y pulsa Iniciar.
2. Abre la aplicación de videollamada **después**.
3. En sus ajustes de video, elige **CamVoice Virtual Camera** como cámara.
4. El micrófono se elige por separado, como siempre: esta aplicación no toca el
   audio de la llamada.

## 6. Dónde se guardan las cosas

```
%APPDATA%\camvoice\
    config.json              configuración
    clipcache\               caché de fotogramas (puede ocupar cientos de MB)
    metrics\                 CSV de las mediciones
```

La caché ocupa unos **7 MB por segundo de clip** a 640 × 480 y 15 FPS. Dos clips
de 8 s son unos 111 MB. Es el precio de no decodificar video en el bucle de
salida; ver `docs/ARQUITECTURA.md` §4.1.

Para vaciarla: `camvoice-cli clean`. Se regenera al volver a arrancar.

## 7. Desinstalación

1. Cierra la aplicación.
2. Quita el registro de la cámara virtual **antes** de borrar nada:

   ```
   camvoice-cli install-camera --uninstall
   ```

   Si borras la carpeta primero, el registro queda huérfano y `camvoice-cli
   cameras` lo marcará como «DLL AUSENTE»; para limpiarlo habría que volver a
   descomprimir y repetir este comando.

3. Borra la carpeta donde descomprimiste `camvoice.exe`.
4. Borra los datos de la aplicación:

   ```
   rmdir /s /q "%APPDATA%\camvoice"
   ```

Con eso no queda nada: el paso 2 borra las entradas de registro del filtro, y
no se instalan servicios, tareas programadas ni archivos fuera de las rutas
anteriores. Puedes confirmarlo con `camvoice-cli cameras` antes de borrar la
carpeta: la cámara ya no debe aparecer.

## 8. Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| «No hay ninguna cámara virtual registrada» | Falta ejecutar `camvoice-cli install-camera`. Ver §1 |
| «La cámara virtual ya está en uso» | Otra aplicación alimenta el mismo filtro (otra copia de camvoice, u OBS con su cámara iniciada). Ciérrala |
| No aparece en Teams/Zoom/Chrome | Cierra y vuelve a abrir esa aplicación: enumera las cámaras al arrancar |
| No aparece en la app Cámara de Windows | Es normal: esa app no soporta DirectShow. Pruébalo en Chrome o Teams |
| «El micrófono no entrega señal audible» | Silenciado en Windows, o volumen de entrada a cero. *Configuración > Sistema > Sonido > Entrada* |
| «No se encuentra el clip» | Se movió o renombró el video. Vuelve a seleccionarlo |
| Imagen congelada | La caché dejó de ser legible (disco extraíble, archivo borrado). `camvoice-cli clean` y volver a iniciar |
| Arranca y se cierra sin más | Lánzalo desde consola con `camvoice-cli doctor` para ver el error |
