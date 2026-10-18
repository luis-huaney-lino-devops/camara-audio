"""Interfaz de línea de comandos.

Existe para que todo sea verificable sin abrir la ventana: diagnóstico del
entorno, preparación de clips, prueba del detector con el micrófono real y
medición de rendimiento. Es el camino que usan los criterios de aceptación.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

from .config import AppConfig, cache_dir, config_path, frame_bytes
from .output.filters import DEFAULT_CAMERA_NAME

__all__ = ["main", "build_parser"]


# --------------------------------------------------------------------------- #
# Utilidades de presentación
# --------------------------------------------------------------------------- #

OK = "[ok]"
FAIL = "[--]"
WARN = "[!!]"


def _bar(value_db: float, floor: float = -60.0, width: int = 24) -> str:
    """Barra de nivel en texto, de `floor` dBFS a 0."""
    norm = (max(floor, min(0.0, value_db)) - floor) / -floor
    filled = int(round(norm * width))
    return "#" * filled + "." * (width - filled)


def _apply_overrides(cfg: AppConfig, args: argparse.Namespace) -> None:
    v, a, o, m = cfg.video, cfg.audio, cfg.output, cfg.metrics
    if getattr(args, "speaking", None):
        v.speaking_clip = str(Path(args.speaking).expanduser().resolve())
    if getattr(args, "silent", None):
        v.silent_clip = str(Path(args.silent).expanduser().resolve())
    if getattr(args, "width", None):
        v.width = args.width
    if getattr(args, "height", None):
        v.height = args.height
    if getattr(args, "fps", None):
        v.fps = args.fps
    if getattr(args, "fit", None):
        v.fit = args.fit
    if getattr(args, "mirror", None) is not None:
        v.mirror = args.mirror
    if getattr(args, "transition", None) is not None:
        v.transition_ms = args.transition
    if getattr(args, "loop_mode", None):
        v.loop_mode = args.loop_mode
    if getattr(args, "pixel_format", None):
        v.pixel_format = args.pixel_format
    if getattr(args, "mic", None) is not None:
        a.device = args.mic
        a.device_name = ""
    if getattr(args, "vad_backend", None):
        cfg.vad.backend = args.vad_backend
    if getattr(args, "aggressiveness", None) is not None:
        cfg.vad.aggressiveness = args.aggressiveness
    if getattr(args, "attack", None) is not None:
        cfg.vad.attack_ms = args.attack
    if getattr(args, "release", None) is not None:
        cfg.vad.release_ms = args.release
    if getattr(args, "no_virtual_cam", False):
        o.enabled = False
    if getattr(args, "backend", None):
        o.backend = args.backend
    if getattr(args, "metrics", False):
        m.enabled = True
    if getattr(args, "metrics_csv", None):
        m.enabled = True
        m.csv_path = str(Path(args.metrics_csv).expanduser())


def _load(args: argparse.Namespace) -> AppConfig:
    path = Path(args.config) if getattr(args, "config", None) else None
    cfg = AppConfig.load(path)
    _apply_overrides(cfg, args)
    if getattr(args, "save", False):
        saved = cfg.save(path)
        print(f"Configuración guardada en {saved}")
    return cfg


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #


def cmd_devices(args: argparse.Namespace) -> int:
    from .audio.capture import AudioError, list_input_devices

    try:
        devices = list_input_devices()
    except AudioError as exc:
        print(f"{FAIL} {exc}")
        return 1
    if not devices:
        print(f"{FAIL} No se encontró ningún dispositivo de entrada de audio.")
        return 1
    print("Dispositivos de entrada disponibles (los de WASAPI primero):\n")
    for dev in devices:
        print(f"  {dev.label}")
        print(f"       canales: {dev.channels}   nativo: {dev.default_samplerate:.0f} Hz")
    print(f"\nÚsalo con:  camvoice run --mic {devices[0].index}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _load(args)
    problems = 0

    print("== Entorno ==")
    print(f"  Python            {sys.version.split()[0]} ({sys.executable})")
    for module, label in (
        ("numpy", "NumPy"),
        ("cv2", "OpenCV"),
        ("sounddevice", "sounddevice"),
        ("pyvirtualcam", "pyvirtualcam"),
        ("psutil", "psutil"),
    ):
        try:
            mod = __import__(module)
            version = getattr(mod, "__version__", "?")
            print(f"  {OK} {label:<16}{version}")
        except Exception as exc:
            print(f"  {FAIL} {label:<16}no disponible ({exc})")
            problems += 1

    from .audio.vad import webrtc_available

    if webrtc_available():
        print(f"  {OK} WebRTC VAD      disponible")
    else:
        print(f"  {WARN} WebRTC VAD      no disponible; se usará el detector RMS")

    print("\n== Audio ==")
    from .audio.capture import AudioError, list_input_devices

    try:
        devices = list_input_devices()
        if devices:
            print(f"  {OK} {len(devices)} dispositivos de entrada")
            print(f"     por defecto: {devices[0].label}")
        else:
            print(f"  {FAIL} Sin dispositivos de entrada")
            problems += 1
    except AudioError as exc:
        print(f"  {FAIL} {exc}")
        problems += 1

    print("\n== Cámara virtual ==")
    from .output.filters import describe_availability
    from .output.sink import diagnose_virtual_camera

    for line in describe_availability().splitlines():
        print(f"  {line}")
    diag = diagnose_virtual_camera(
        cfg.output, width=cfg.video.width, height=cfg.video.height, fps=cfg.video.fps
    )
    if diag.available:
        print(f"  {OK} {diag.detail}")
    else:
        print(f"  {FAIL} {diag.detail}")
        problems += 1

    print("\n== Configuración ==")
    print(f"  archivo           {config_path()}")
    print(f"  caché de clips    {cache_dir()}")
    bpf = frame_bytes(cfg.video.width, cfg.video.height, cfg.video.pixel_format)
    print(
        f"  salida            {cfg.video.width}x{cfg.video.height} a {cfg.video.fps} FPS "
        f"({cfg.video.pixel_format.upper()}, {bpf / 1024:.0f} KB/fotograma, "
        f"{bpf * cfg.video.fps / 1e6:.1f} MB/s de caché)"
    )
    errors = cfg.validate(require_clips=False)
    for err in errors:
        print(f"  {FAIL} {err}")
        problems += 1
    for warn in cfg.warnings():
        print(f"  {WARN} {warn}")
    for label, clip in (("habla", cfg.video.speaking_clip), ("silencio", cfg.video.silent_clip)):
        if not clip:
            print(f"  {WARN} clip de {label}: sin seleccionar")
        elif not Path(clip).exists():
            print(f"  {FAIL} clip de {label}: no se encuentra {clip}")
            problems += 1
        else:
            print(f"  {OK} clip de {label}: {clip}")

    print()
    if problems:
        print(f"{problems} problema(s) que impiden funcionar por completo.")
        return 1
    print("Todo listo.")
    return 0


def cmd_cameras(args: argparse.Namespace) -> int:
    """Lista las cámaras que Windows tiene registradas, reales y virtuales."""
    from .output.filters import bundled_filters, detect_backends, filters_dir, list_directshow_cameras

    cameras = list_directshow_cameras()
    if not cameras:
        print(f"{FAIL} No se pudo enumerar ninguna cámara DirectShow.")
        return 1

    virtual = [c for c in cameras if c.backend]
    physical = [c for c in cameras if not c.backend]

    print("Cámaras virtuales que esta aplicación puede alimentar:\n")
    if virtual:
        for cam in virtual:
            print(f"  {OK if cam.dll_exists else FAIL} {cam.label}")
            print(f"       CLSID {cam.clsid}")
            print(f"       DLL   {cam.dll_path or '(sin ruta)'}")
    else:
        print(f"  {FAIL} Ninguna. Ejecuta «camvoice-cli install-camera».")

    if physical:
        print("\nOtras fuentes de video del sistema (cámaras reales):\n")
        for cam in physical:
            print(f"  - {cam.friendly_name} ({cam.architecture})")

    arches = {c.architecture for c in virtual}
    if virtual and "32-bit" not in arches:
        print(
            f"\n  {WARN} Solo hay filtro de 64 bits registrado. Una aplicación de "
            "videollamada de 32 bits no verá la cámara."
        )

    print(f"\nBackends utilizables: {', '.join(detect_backends()) or 'ninguno'}")
    bundled = bundled_filters()
    print(
        "Filtro propio incluido: "
        + (", ".join(p.name for p in bundled.values()) if bundled else f"no ({filters_dir()})")
    )
    return 0


def cmd_install_camera(args: argparse.Namespace) -> int:
    """Registra el filtro de cámara virtual que acompaña a la aplicación."""
    from .output.filters import FilterError, obs_install_script, register_filter, unregister_filter

    dll = Path(args.dll) if args.dll else None
    try:
        if args.uninstall:
            message = unregister_filter(dll)
        else:
            message = register_filter(dll, camera_name=args.name)
    except FilterError as exc:
        print(f"{FAIL} {exc}")
        script = obs_install_script()
        if script and not args.uninstall:
            print(
                f"\n{WARN} Alternativa: tienes OBS Studio instalado. Puedes "
                "registrar su filtro ejecutando como administrador:\n"
                f'  "{script}"'
            )
        return 1

    print(f"{OK} {message}")
    print("\nComprobando el resultado...")
    from .output.filters import describe_availability

    print(describe_availability())
    print(
        "\nRecuerda: las aplicaciones de videollamada enumeran las cámaras al "
        "arrancar. Cierra y vuelve a abrir Teams, Zoom o el navegador."
    )
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    from .video.cache import ClipCacheError, probe_clip

    rc = 0
    for raw in args.videos:
        try:
            probe = probe_clip(raw)
        except ClipCacheError as exc:
            print(f"{FAIL} {raw}: {exc}")
            rc = 1
            continue
        print(f"{OK} {Path(raw).name}")
        print(f"     resolución   {probe.width}x{probe.height} ({probe.aspect:.3f}:1)")
        print(f"     cadencia     {probe.fps:.3f} FPS")
        print(f"     fotogramas   {probe.frame_count} ({probe.duration_s:.2f} s)")
        print(f"     códec        {probe.fourcc or 'desconocido'}")
    return rc


def cmd_prepare(args: argparse.Namespace) -> int:
    from .controller import Session, SessionError

    cfg = _load(args)
    session = Session(cfg)
    last = [-1]

    def progress(done: int, total: int, message: str) -> None:
        if total and done != last[0]:
            pct = min(100, int(done * 100 / total))
            print(f"\r  {message}: {pct:3d}% ({done}/{total})", end="", flush=True)
            last[0] = done
        elif not total:
            print(f"\r  {message}: {done} fotogramas", end="", flush=True)

    try:
        clips = session.prepare(progress=progress, force=args.force)
    except SessionError as exc:
        print(f"\n{FAIL} {exc}")
        return 1
    print()
    print(f"{OK} {clips.speaking.describe()}")
    print(f"{OK} {clips.silent.describe()}")
    print(f"     caché total: {clips.total_mb:.0f} MB en {cache_dir()}")
    for warn in clips.warnings:
        print(f"  {WARN} {warn}")
    session.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .controller import Session, SessionError

    cfg = _load(args)
    session = Session(cfg)

    print("Preparando clips...")
    try:
        session.prepare(force=args.force, load_to_ram=args.ram)
    except SessionError as exc:
        print(f"{FAIL} {exc}")
        return 1
    assert session.clips is not None
    print(f"  {session.clips.speaking.describe()}")
    print(f"  {session.clips.silent.describe()}")
    for warn in session.clips.warnings:
        print(f"  {WARN} {warn}")

    stop = {"now": False}

    def handle(signum, frame):  # noqa: ANN001
        stop["now"] = True

    signal.signal(signal.SIGINT, handle)

    session.start()
    started = time.perf_counter()
    label = f"{args.duration:.0f} s" if args.duration else "Ctrl+C para detener"
    print(f"\nEn marcha ({label}).\n")

    try:
        while not stop["now"]:
            if args.duration and time.perf_counter() - started >= args.duration:
                break
            status = session.status()
            if args.quiet:
                time.sleep(0.5)
                continue
            v, r = status.voice, status.render
            line = (
                f"\r{status.headline:<16} "
                f"nivel {_bar(v.rms_db)} {v.rms_db:6.1f} dB  "
                f"piso {v.noise_floor_db:6.1f}  "
                f"FPS {r.fps_effective:5.2f}  "
                f"ms/frame {r.avg_frame_ms:5.2f}  "
                f"cambios {r.transitions:3d}  "
            )
            sys.stdout.write(line[:150])
            sys.stdout.flush()
            if v.error:
                print(f"\n{FAIL} audio: {v.error}")
            elif v.signal_lost:
                print(f"\n{WARN} {v.hint}")
            if r.error:
                print(f"\n{FAIL} salida: {r.error}")
            if r.warning:
                print(f"\n{WARN} {r.warning}")
            time.sleep(0.2)
    finally:
        print("\nDeteniendo...")
        # El estado se lee ANTES de parar: `stop()` descarta el motor y a partir
        # de ahí `status()` devuelve la instantánea vacía por defecto, de modo
        # que el resumen informaría siempre de cero fotogramas.
        status = session.status()
        summary = session.metrics.summary() if session.metrics else None
        session.stop()
        print(f"  fotogramas emitidos: {status.render.frames_sent}")
        print(f"  cambios de estado:   {status.render.transitions}")
        print(f"  FPS efectivos:       {status.render.fps_effective:.2f}")
        if summary is not None:
            print()
            print(summary.render())
        session.close()
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Mide el régimen estacionario con y sin cámara virtual."""
    from .controller import Session, SessionError
    from .output.sink import NullSink

    cfg = _load(args)
    cfg.metrics.enabled = True
    cfg.metrics.interval_s = min(cfg.metrics.interval_s, 1.0)

    session = Session(cfg)
    print("Preparando clips...")
    try:
        session.prepare(force=args.force, load_to_ram=args.ram)
    except SessionError as exc:
        print(f"{FAIL} {exc}")
        return 1

    sink = NullSink() if args.no_virtual_cam else None
    target = "sin salida (coste del motor)" if sink else "cámara virtual"
    print(f"Midiendo {args.duration:.0f} s con {target}...")
    print("Habla y calla alternadamente si quieres incluir transiciones.\n")

    session.start(sink=sink)
    deadline = time.perf_counter() + args.duration
    try:
        while time.perf_counter() < deadline:
            remaining = deadline - time.perf_counter()
            status = session.status()
            sys.stdout.write(
                f"\r  {remaining:5.0f} s restantes   estado {status.headline:<16} "
                f"FPS {status.render.fps_effective:5.2f}   "
                f"ms/frame {status.render.avg_frame_ms:5.2f}"
            )
            sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    finally:
        print("\n")
        summary = session.metrics.summary() if session.metrics else None
        session.stop()
        session.close()

    if summary is None:
        print(f"{FAIL} No se recogieron métricas.")
        return 1
    print(summary.render())

    target_fps = cfg.video.fps
    print()
    verdicts = [
        (
            "FPS sostenidos",
            summary.fps_mean >= target_fps * 0.97,
            f"{summary.fps_mean:.2f} frente a {target_fps} objetivo",
        ),
        (
            "CPU bajo el 25 %",
            summary.cpu_mean < 25.0,
            f"{summary.cpu_mean:.1f} % de un núcleo (p95 {summary.cpu_p95:.1f} %) = "
            f"{summary.cpu_norm_mean:.1f} % de la máquina",
        ),
        ("Memoria estable", summary.memory_stable, f"{summary.rss_slope_mb_per_min:+.2f} MB/min"),
    ]
    for name, ok, detail in verdicts:
        print(f"  {OK if ok else FAIL} {name:<22}{detail}")
    return 0 if all(v[1] for v in verdicts) else 2


def cmd_vad_test(args: argparse.Namespace) -> int:
    """Escucha el micrófono y muestra la cronología del detector.

    Es la herramienta para ajustar sensibilidad (CA-04 y CA-05) sin necesidad de
    los videos ni de la cámara virtual.
    """
    from .audio.pipeline import AudioPipeline

    cfg = _load(args)
    pipeline = AudioPipeline(cfg)
    pipeline.start()

    print(f"Escuchando {args.duration:.0f} s. Habla, calla y haz ruido de fondo.\n")
    print(f"{'t (s)':>7}  {'estado':<9} {'trama':<7} {'nivel':>7} {'piso':>7} {'SNR':>6}  medidor")

    started = time.perf_counter()
    changes: list[tuple[float, str]] = []
    previous = None
    try:
        while time.perf_counter() - started < args.duration:
            s = pipeline.status()
            t = time.perf_counter() - started
            if s.error:
                print(f"\n{FAIL} {s.error}")
                time.sleep(1.0)
                continue
            if previous is None:
                previous = s.state.value  # el estado inicial no es un cambio
            elif s.state.value != previous:
                changes.append((t, s.state.value))
                previous = s.state.value
            print(
                f"{t:7.2f}  {s.state.value:<9} "
                f"{'voz' if s.voiced else 'silencio':<7} "
                f"{s.rms_db:7.1f} {s.noise_floor_db:7.1f} {s.snr_db:6.1f}  {_bar(s.rms_db)}"
                + ("  calibrando" if s.calibrating else ""),
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()

    status = pipeline.status()
    if status.hint:
        print(f"\n{WARN} {status.hint}")
    print(f"\nBackend: {status.backend}   dispositivo: {status.device_name}")
    print(
        f"Audio a {status.samplerate} Hz"
        + (f" (remuestreado desde {status.resampled_from} Hz)" if status.resampled_from != status.samplerate else "")
    )
    print(f"Tramas analizadas: {status.frames}   desbordes: {status.overflows}")
    print(f"Cambios de estado: {len(changes)}")
    for t, state in changes:
        print(f"  {t:6.2f} s -> {state}")
    if not changes:
        print("  (ninguno: el detector se mantuvo estable)")
    if status.frames:
        elapsed = time.perf_counter() - started
        rate = len(changes) / max(1e-6, elapsed / 60.0)
        print(f"\nTasa de cambios: {rate:.1f} por minuto")
        if rate > 30:
            print(f"{WARN} Demasiados cambios: sube --aggressiveness o --release.")
    return 0


def cmd_make_clips(args: argparse.Namespace) -> int:
    from .testclips import make_test_clips

    speaking, silent = make_test_clips(
        Path(args.outdir),
        width=args.width,
        height=args.height,
        fps=args.fps,
        seconds=args.seconds,
    )
    print(f"{OK} clip de habla:    {speaking}")
    print(f"{OK} clip de silencio: {silent}")
    print("\nSon clips sintéticos para validar la cadena completa sin grabar nada.")
    print("Pruébalos con:")
    print(f'  camvoice run --speaking "{speaking}" --silent "{silent}"')
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .ui.app import run_gui

    cfg = _load(args)
    return run_gui(cfg, config_file=Path(args.config) if args.config else None)


def cmd_config(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else config_path()
    if args.reset:
        if path.exists():
            path.unlink()
            print(f"{OK} Configuración borrada: {path}")
        else:
            print("No había configuración guardada.")
        return 0
    cfg = AppConfig.load(path)
    import json

    print(f"# {path}{'' if path.exists() else '  (no existe: valores por defecto)'}")
    print(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    from .video.cache import prune_cache

    freed = prune_cache(set(), directory=cache_dir())
    print(f"{OK} Liberados {freed / 1e6:.0f} MB de {cache_dir()}")
    return 0


# --------------------------------------------------------------------------- #
# Definición del parser
# --------------------------------------------------------------------------- #


def _add_clip_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--speaking", help="Video del usuario hablando.")
    p.add_argument("--silent", help="Video del usuario en silencio.")
    p.add_argument("--width", type=int, help="Ancho de salida (por defecto 640).")
    p.add_argument("--height", type=int, help="Alto de salida (por defecto 480).")
    p.add_argument("--fps", type=int, help="FPS de salida (por defecto 15).")
    p.add_argument("--fit", choices=("cover", "contain", "stretch"), help="Ajuste del encuadre.")
    p.add_argument("--mirror", action="store_true", default=None, help="Espejo horizontal.")
    p.add_argument("--transition", type=int, metavar="MS", help="Duración del fundido.")
    p.add_argument("--loop-mode", choices=("pingpong", "loop"), dest="loop_mode")
    p.add_argument("--pixel-format", choices=("nv12", "bgr"), dest="pixel_format")
    p.add_argument("--force", action="store_true", help="Regenera la caché de clips.")
    p.add_argument("--ram", action="store_true", help="Carga la caché entera en RAM.")


def _add_audio_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mic", type=int, metavar="N", help="Índice de micrófono (ver «devices»).")
    p.add_argument("--vad-backend", choices=("auto", "webrtc", "rms"), dest="vad_backend")
    p.add_argument("--aggressiveness", type=int, choices=(0, 1, 2, 3))
    p.add_argument("--attack", type=int, metavar="MS", help="Voz necesaria para activar.")
    p.add_argument("--release", type=int, metavar="MS", help="Silencio necesario para desactivar.")


def _add_output_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--no-virtual-cam", action="store_true", help="No abrir la cámara virtual.")
    p.add_argument("--backend", help="Backend de pyvirtualcam (obs, unitycapture).")
    p.add_argument("--metrics", action="store_true", help="Registrar métricas en CSV.")
    p.add_argument("--metrics-csv", help="Ruta del CSV de métricas.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="camvoice",
        description=(
            "Cámara virtual que reproduce un clip pregrabado tuyo hablando cuando "
            "detecta tu voz y otro en silencio cuando callas."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  camvoice doctor\n"
            "  camvoice devices\n"
            "  camvoice make-clips --outdir .\\clips\n"
            '  camvoice run --speaking hablando.mp4 --silent quieto.mp4 --save\n'
            "  camvoice vad-test --duration 30\n"
            "  camvoice bench --duration 1800 --metrics\n"
        ),
    )
    parser.add_argument("--config", help="Ruta alternativa del archivo de configuración.")
    parser.add_argument("--save", action="store_true", help="Guarda las opciones dadas.")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("gui", help="Abre la interfaz gráfica (acción por defecto).")
    _add_clip_args(p)
    _add_audio_args(p)
    _add_output_args(p)
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("doctor", help="Comprueba entorno, audio y cámara virtual.")
    _add_output_args(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("devices", help="Lista los micrófonos disponibles.")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("cameras", help="Lista las cámaras registradas en Windows.")
    p.set_defaults(func=cmd_cameras)

    p = sub.add_parser(
        "install-camera",
        help="Registra el filtro de cámara virtual propio (pide permisos de administrador).",
    )
    p.add_argument("--dll", help="DLL concreta a registrar; por defecto la incluida.")
    p.add_argument(
        "--name",
        default=DEFAULT_CAMERA_NAME,
        help=f"Nombre con el que aparecerá la cámara (por defecto: {DEFAULT_CAMERA_NAME!r}).",
    )
    p.add_argument("--uninstall", action="store_true", help="Quitar el registro.")
    p.set_defaults(func=cmd_install_camera)

    p = sub.add_parser("probe", help="Muestra las propiedades de uno o más videos.")
    p.add_argument("videos", nargs="+")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("prepare", help="Prepara la caché de fotogramas de los clips.")
    _add_clip_args(p)
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("run", help="Ejecuta sin interfaz gráfica.")
    _add_clip_args(p)
    _add_audio_args(p)
    _add_output_args(p)
    p.add_argument("--duration", type=float, default=0.0, metavar="S", help="0 = indefinido.")
    p.add_argument("--quiet", action="store_true", help="Sin línea de estado continua.")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("bench", help="Mide CPU, memoria y FPS durante un periodo.")
    _add_clip_args(p)
    _add_audio_args(p)
    _add_output_args(p)
    p.add_argument("--duration", type=float, default=120.0, metavar="S")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("vad-test", help="Prueba el detector de voz con el micrófono.")
    _add_audio_args(p)
    p.add_argument("--duration", type=float, default=30.0, metavar="S")
    p.add_argument("--interval", type=float, default=0.25, metavar="S")
    p.set_defaults(func=cmd_vad_test)

    p = sub.add_parser("make-clips", help="Genera clips sintéticos de prueba.")
    p.add_argument("--outdir", default="clips")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--seconds", type=float, default=6.0)
    p.set_defaults(func=cmd_make_clips)

    p = sub.add_parser("config", help="Muestra o borra la configuración guardada.")
    p.add_argument("--reset", action="store_true")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("clean", help="Borra la caché de fotogramas.")
    p.set_defaults(func=cmd_clean)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # Sin subcomando se abre la interfaz: es lo que espera quien hace doble
        # clic en el ejecutable.
        args = parser.parse_args(["gui", *(argv or [])])
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrumpido.")
        return 130
