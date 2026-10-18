"""Interfaz gráfica (PySide6).

Reglas de la ventana:

* La UI nunca genera fotogramas ni toca el audio. Un único `QTimer` lee el
  estado publicado por los hilos de audio y render y refresca lo que ha
  cambiado. Así ningún arrastre de un deslizador puede provocar un tirón en la
  salida.
* La preparación de clips (lo único que tarda) corre en un hilo aparte con
  barra de progreso: la ventana sigue respondiendo.
* Los controles se separan en dos clases. Los que se aplican en caliente
  (sensibilidad, tiempos, velocidades, transición, cámara virtual) quedan
  activos mientras la sesión corre. Los que invalidan la caché de fotogramas
  (clips, resolución, FPS, encuadre, espejo) se bloquean con una explicación,
  en vez de fingir que surten efecto.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..audio.capture import AudioError, DeviceInfo, list_input_devices
from ..config import AppConfig
from ..controller import Session, SessionError
from ..video.cache import ClipCacheError, probe_clip

VIDEO_FILTER = "Videos (*.mp4 *.mov *.avi *.mkv *.webm *.m4v);;Todos los archivos (*.*)"

STATE_STYLES = {
    "SILENT": ("Silencio", "#2d3b4a", "#9fb4c7"),
    "SPEAKING": ("Voz detectada", "#1f4d2b", "#7fe0a0"),
    "TRANSITION": ("Transición", "#4d3f1f", "#e8c97f"),
}

RESOLUTIONS = [
    ("640 x 480  (4:3, recomendada)", 640, 480),
    ("848 x 480  (16:9)", 848, 480),
    ("960 x 720  (4:3)", 960, 720),
    ("1280 x 720 (16:9)", 1280, 720),
]


# --------------------------------------------------------------------------- #
# Hilo de preparación
# --------------------------------------------------------------------------- #


class PrepareWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object, str)
    """(PreparedClips | None, mensaje de error)"""

    def __init__(self, session: Session, force: bool) -> None:
        super().__init__()
        self._session = session
        self._force = force

    def run(self) -> None:
        try:
            clips = self._session.prepare(
                progress=lambda d, t, m: self.progress.emit(d, t, m), force=self._force
            )
        except (SessionError, ClipCacheError) as exc:
            self.finished.emit(None, str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensivo
            self.finished.emit(None, f"Fallo inesperado al preparar los clips: {exc}")
            return
        self.finished.emit(clips, "")


# --------------------------------------------------------------------------- #
# Ventana
# --------------------------------------------------------------------------- #


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig, config_file: Path | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.config_file = config_file
        self.session = Session(cfg)
        self._devices: list[DeviceInfo] = []
        self._thread: QThread | None = None
        self._worker: PrepareWorker | None = None
        self._loading = True
        self._last_error = ""

        self.setWindowTitle("Cámara virtual por voz")
        self.resize(1080, 700)
        self._build()
        self._load_devices()
        self._load_from_config()
        self._loading = False
        self._update_clip_info()
        self._set_running(False)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(max(33, int(1000 / max(1, cfg.video.preview_fps))))

    # ------------------------------------------------------------------ #
    # Construcción
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)
        root.addWidget(self._build_left(), 3)
        root.addWidget(self._build_right(), 2)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Listo.")

    # --- columna izquierda: vista previa y estado ---------------------- #

    def _build_left(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self.state_badge = QLabel("Detenido")
        self.state_badge.setAlignment(Qt.AlignCenter)
        self.state_badge.setMinimumHeight(44)
        self._style_badge("-")
        layout.addWidget(self.state_badge)

        self.preview = QLabel("La vista previa aparecerá al iniciar.")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(480, 360)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setStyleSheet(
            "background:#15181c; color:#7d8893; border:1px solid #2a2f36; border-radius:6px;"
        )
        layout.addWidget(self.preview, 1)

        meter_row = QHBoxLayout()
        meter_row.addWidget(QLabel("Nivel"))
        self.level_meter = QProgressBar()
        self.level_meter.setRange(-60, 0)
        self.level_meter.setValue(-60)
        self.level_meter.setFormat("%v dBFS")
        meter_row.addWidget(self.level_meter, 1)
        layout.addLayout(meter_row)

        self.detector_info = QLabel("Detector detenido.")
        self.detector_info.setStyleSheet("color:#8b949e;")
        layout.addWidget(self.detector_info)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.error_banner = QLabel()
        self.error_banner.setWordWrap(True)
        self.error_banner.setVisible(False)
        self.error_banner.setFrameShape(QFrame.StyledPanel)
        self.error_banner.setStyleSheet(
            "background:#3a1f22; color:#ffb3b8; padding:8px; border-radius:4px;"
        )
        layout.addWidget(self.error_banner)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("Iniciar")
        self.start_button.setMinimumHeight(38)
        self.start_button.clicked.connect(self._on_start)
        buttons.addWidget(self.start_button, 2)

        self.stop_button = QPushButton("Detener")
        self.stop_button.setMinimumHeight(38)
        self.stop_button.clicked.connect(self._on_stop)
        buttons.addWidget(self.stop_button, 1)
        layout.addLayout(buttons)

        self.metrics_label = QLabel("-")
        self.metrics_label.setStyleSheet("color:#8b949e; font-family:Consolas,monospace;")
        layout.addWidget(self.metrics_label)
        return panel

    # --- columna derecha: configuración ------------------------------- #

    def _build_right(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 8, 0)

        layout.addWidget(self._group_clips())
        layout.addWidget(self._group_audio())
        layout.addWidget(self._group_detector())
        layout.addWidget(self._group_output())
        layout.addStretch(1)

        save = QPushButton("Guardar configuración")
        save.clicked.connect(self._on_save)
        layout.addWidget(save)

        scroll.setWidget(inner)
        return scroll

    def _group_clips(self) -> QGroupBox:
        box = QGroupBox("1. Videos de referencia")
        layout = QVBoxLayout(box)

        self.speaking_path = QLabel("(sin seleccionar)")
        self.silent_path = QLabel("(sin seleccionar)")
        for label in (self.speaking_path, self.silent_path):
            label.setWordWrap(True)
            label.setStyleSheet("color:#8b949e;")

        for title, label, slot in (
            ("Clip hablando...", self.speaking_path, self._pick_speaking),
            ("Clip en silencio...", self.silent_path, self._pick_silent),
        ):
            button = QPushButton(title)
            button.clicked.connect(slot)
            layout.addWidget(button)
            layout.addWidget(label)
            if title.startswith("Clip hablando"):
                self.speaking_button = button
            else:
                self.silent_button = button

        self.clip_info = QLabel()
        self.clip_info.setWordWrap(True)
        self.clip_info.setStyleSheet("color:#8b949e; font-size:11px;")
        layout.addWidget(self.clip_info)
        return box

    def _group_audio(self) -> QGroupBox:
        box = QGroupBox("2. Micrófono")
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.mic_combo = QComboBox()
        self.mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        row.addWidget(self.mic_combo, 1)
        refresh = QPushButton("Actualizar")
        refresh.clicked.connect(self._load_devices)
        row.addWidget(refresh)
        layout.addLayout(row)
        hint = QLabel("Se puede cambiar con la sesión en marcha.")
        hint.setStyleSheet("color:#8b949e; font-size:11px;")
        layout.addWidget(hint)
        return box

    def _group_detector(self) -> QGroupBox:
        box = QGroupBox("3. Detección de voz")
        form = QFormLayout(box)

        self.vad_backend = QComboBox()
        self.vad_backend.addItem("Automático (WebRTC si está disponible)", "auto")
        self.vad_backend.addItem("WebRTC VAD", "webrtc")
        self.vad_backend.addItem("Energía (RMS adaptativo)", "rms")
        self.vad_backend.currentIndexChanged.connect(
            lambda: self._apply_vad(backend=self.vad_backend.currentData())
        )
        form.addRow("Detector", self.vad_backend)

        self.aggressiveness = self._slider(0, 3, 1, lambda v: self._apply_vad(aggressiveness=v))
        form.addRow("Exigencia (0-3)", self._with_value(self.aggressiveness, "{}"))

        self.snr_on = self._slider(3, 25, 1, lambda v: self._apply_vad(snr_on_db=float(v)))
        form.addRow("Umbral de voz", self._with_value(self.snr_on, "{} dB sobre el ruido"))

        self.attack = self._slider(20, 600, 10, lambda v: self._apply_vad(attack_ms=v))
        form.addRow("Tiempo de activación", self._with_value(self.attack, "{} ms"))

        self.release = self._slider(100, 2000, 50, lambda v: self._apply_vad(release_ms=v))
        form.addRow("Permanencia en voz", self._with_value(self.release, "{} ms"))

        self.pause_tol = self._slider(0, 1200, 50, lambda v: self._apply_vad(pause_tolerance_ms=v))
        form.addRow("Tolerancia a pausas", self._with_value(self.pause_tol, "{} ms"))
        return box

    def _group_output(self) -> QGroupBox:
        box = QGroupBox("4. Salida")
        form = QFormLayout(box)

        self.resolution = QComboBox()
        for label, w, h in RESOLUTIONS:
            self.resolution.addItem(label, (w, h))
        self.resolution.currentIndexChanged.connect(self._on_geometry_changed)
        form.addRow("Resolución", self.resolution)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(5, 60)
        self.fps_spin.valueChanged.connect(self._on_geometry_changed)
        form.addRow("FPS", self.fps_spin)

        self.fit_combo = QComboBox()
        self.fit_combo.addItem("Recortar para llenar (sin deformar)", "cover")
        self.fit_combo.addItem("Caber entero (con barras)", "contain")
        self.fit_combo.addItem("Estirar (deforma)", "stretch")
        self.fit_combo.currentIndexChanged.connect(self._on_geometry_changed)
        form.addRow("Encuadre", self.fit_combo)

        self.mirror_check = QCheckBox("Efecto espejo")
        self.mirror_check.stateChanged.connect(self._on_geometry_changed)
        form.addRow("", self.mirror_check)

        self.transition = self._slider(0, 600, 20, self._on_live_video)
        form.addRow("Transición", self._with_value(self.transition, "{} ms"))

        self.loop_combo = QComboBox()
        self.loop_combo.addItem("Ida y vuelta (sin costura)", "pingpong")
        self.loop_combo.addItem("Bucle normal", "loop")
        self.loop_combo.currentIndexChanged.connect(lambda: self._on_live_video(0))
        form.addRow("Bucle", self.loop_combo)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.25, 2.0)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.valueChanged.connect(lambda: self._on_live_video(0))
        form.addRow("Velocidad al hablar", self.speed_spin)

        self.virtual_check = QCheckBox("Enviar a la cámara virtual")
        self.virtual_check.stateChanged.connect(self._on_virtual_toggled)
        form.addRow("", self.virtual_check)

        self.virtual_status = QLabel()
        self.virtual_status.setWordWrap(True)
        self.virtual_status.setStyleSheet("color:#8b949e; font-size:11px;")
        form.addRow("", self.virtual_status)

        check = QPushButton("Comprobar cámara virtual")
        check.clicked.connect(self._on_check_camera)
        form.addRow("", check)
        return box

    # --- helpers de widgets ------------------------------------------- #

    def _slider(self, low: int, high: int, step: int, on_change) -> QSlider:  # noqa: ANN001
        slider = QSlider(Qt.Horizontal)
        slider.setRange(low, high)
        slider.setSingleStep(step)
        slider.setPageStep(step * 2)
        slider.valueChanged.connect(lambda v: None if self._loading else on_change(v))
        return slider

    def _with_value(self, slider: QSlider, template: str) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slider, 1)
        label = QLabel()
        label.setMinimumWidth(130)
        label.setStyleSheet("color:#8b949e;")
        layout.addWidget(label)

        def update(value: int) -> None:
            label.setText(template.format(value))

        slider.valueChanged.connect(update)
        update(slider.value())
        return wrapper

    # ------------------------------------------------------------------ #
    # Carga y guardado
    # ------------------------------------------------------------------ #

    def _load_devices(self) -> None:
        previous = self.mic_combo.currentData() if self.mic_combo.count() else None
        self._loading, prior = True, self._loading
        self.mic_combo.clear()
        try:
            self._devices = list_input_devices()
        except AudioError as exc:
            self._devices = []
            self._show_error(str(exc))
        self.mic_combo.addItem("Dispositivo predeterminado de Windows", None)
        for dev in self._devices:
            self.mic_combo.addItem(dev.label, dev.index)
        target = previous if previous is not None else self.cfg.audio.device
        index = self.mic_combo.findData(target)
        self.mic_combo.setCurrentIndex(max(0, index))
        self._loading = prior

    def _load_from_config(self) -> None:
        v, d, o = self.cfg.video, self.cfg.vad, self.cfg.output
        self.speaking_path.setText(v.speaking_clip or "(sin seleccionar)")
        self.silent_path.setText(v.silent_clip or "(sin seleccionar)")

        index = self.resolution.findData((v.width, v.height))
        if index < 0:
            self.resolution.addItem(f"{v.width} x {v.height}", (v.width, v.height))
            index = self.resolution.count() - 1
        self.resolution.setCurrentIndex(index)
        self.fps_spin.setValue(v.fps)
        self.fit_combo.setCurrentIndex(max(0, self.fit_combo.findData(v.fit)))
        self.mirror_check.setChecked(v.mirror)
        self.transition.setValue(v.transition_ms)
        self.loop_combo.setCurrentIndex(max(0, self.loop_combo.findData(v.loop_mode)))
        self.speed_spin.setValue(v.speaking_speed)
        self.virtual_check.setChecked(o.enabled)

        self.vad_backend.setCurrentIndex(max(0, self.vad_backend.findData(d.backend)))
        self.aggressiveness.setValue(d.aggressiveness)
        self.snr_on.setValue(int(d.snr_on_db))
        self.attack.setValue(d.attack_ms)
        self.release.setValue(d.release_ms)
        self.pause_tol.setValue(d.pause_tolerance_ms)

    def _collect_into_config(self) -> None:
        v, o = self.cfg.video, self.cfg.output
        width, height = self.resolution.currentData()
        v.width, v.height = int(width), int(height)
        v.fps = self.fps_spin.value()
        v.fit = self.fit_combo.currentData()
        v.mirror = self.mirror_check.isChecked()
        v.transition_ms = self.transition.value()
        v.loop_mode = self.loop_combo.currentData()
        v.speaking_speed = float(self.speed_spin.value())
        o.enabled = self.virtual_check.isChecked()
        self.cfg.audio.device = self.mic_combo.currentData()
        self.cfg.audio.device_name = next(
            (d.name for d in self._devices if d.index == self.cfg.audio.device), ""
        )

    def _on_save(self) -> None:
        self._collect_into_config()
        errors = self.cfg.validate(require_clips=False)
        if errors:
            QMessageBox.warning(self, "Configuración incompleta", "\n".join(errors))
            return
        path = self.cfg.save(self.config_file)
        self.statusBar().showMessage(f"Guardado en {path}", 5000)

    # ------------------------------------------------------------------ #
    # Selección de clips
    # ------------------------------------------------------------------ #

    def _pick_speaking(self) -> None:
        self._pick_clip("Selecciona el video en el que hablas", "speaking")

    def _pick_silent(self) -> None:
        self._pick_clip("Selecciona el video en el que estás en silencio", "silent")

    def _pick_clip(self, title: str, which: str) -> None:
        start = self.cfg.video.speaking_clip or self.cfg.video.silent_clip or ""
        path, _ = QFileDialog.getOpenFileName(
            self, title, str(Path(start).parent) if start else "", VIDEO_FILTER
        )
        if not path:
            return
        try:
            probe_clip(path)
        except ClipCacheError as exc:
            QMessageBox.critical(self, "Video no utilizable", str(exc))
            return
        if which == "speaking":
            self.cfg.video.speaking_clip = path
            self.speaking_path.setText(path)
        else:
            self.cfg.video.silent_clip = path
            self.silent_path.setText(path)
        self._update_clip_info()

    def _update_clip_info(self) -> None:
        lines: list[str] = []
        for label, raw in (
            ("Hablando", self.cfg.video.speaking_clip),
            ("Silencio", self.cfg.video.silent_clip),
        ):
            if not raw:
                continue
            try:
                probe = probe_clip(raw)
            except ClipCacheError as exc:
                lines.append(f"{label}: {exc}")
                continue
            lines.append(
                f"{label}: {probe.width}x{probe.height}, {probe.fps:.1f} FPS, "
                f"{probe.duration_s:.1f} s, {probe.fourcc or '?'}"
            )
        self.clip_info.setText("\n".join(lines) or "Selecciona los dos videos para continuar.")

    # ------------------------------------------------------------------ #
    # Cambios en caliente
    # ------------------------------------------------------------------ #

    def _apply_vad(self, **changes) -> None:
        if self._loading:
            return
        for key, value in changes.items():
            setattr(self.cfg.vad, key, value)
        # El umbral de apagado sigue al de encendido para que no se invierta.
        changes.setdefault("snr_off_db", max(1.0, self.cfg.vad.snr_on_db - 4.0))
        self.cfg.vad.snr_off_db = changes["snr_off_db"]
        try:
            self.session.audio.apply_vad_changes(**changes)
        except AttributeError:
            pass

    def _on_live_video(self, _value: int = 0) -> None:
        if self._loading:
            return
        v = self.cfg.video
        v.transition_ms = self.transition.value()
        v.loop_mode = self.loop_combo.currentData()
        v.speaking_speed = float(self.speed_spin.value())

    def _on_mic_changed(self) -> None:
        if self._loading:
            return
        index = self.mic_combo.currentData()
        name = next((d.name for d in self._devices if d.index == index), "")
        self.cfg.audio.device = index
        self.cfg.audio.device_name = name
        if self.session.audio.running:
            self.session.audio.set_device(index, name)
            self.statusBar().showMessage("Cambiando de micrófono...", 3000)

    def _on_geometry_changed(self) -> None:
        if self._loading:
            return
        self._collect_into_config()
        if self.session.running:
            self.statusBar().showMessage(
                "La resolución, los FPS y el encuadre se aplican al volver a iniciar.", 6000
            )

    def _on_virtual_toggled(self) -> None:
        if self._loading:
            return
        enabled = self.virtual_check.isChecked()
        self.cfg.output.enabled = enabled
        if self.session.engine is not None:
            self.session.engine.set_virtual_output(enabled)

    def _on_check_camera(self) -> None:
        from ..output.sink import diagnose_virtual_camera

        self._collect_into_config()
        diag = diagnose_virtual_camera(
            self.cfg.output,
            width=self.cfg.video.width,
            height=self.cfg.video.height,
            fps=self.cfg.video.fps,
        )
        if diag.available:
            QMessageBox.information(self, "Cámara virtual", diag.detail)
        else:
            QMessageBox.warning(self, "Cámara virtual no disponible", diag.detail)

    # ------------------------------------------------------------------ #
    # Arranque y parada
    # ------------------------------------------------------------------ #

    def _on_start(self) -> None:
        self._collect_into_config()
        errors = self.cfg.validate()
        if errors:
            QMessageBox.warning(
                self, "Falta configuración", "\n".join(f"- {e}" for e in errors)
            )
            return

        self.session.cfg = self.cfg
        self.start_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.statusBar().showMessage("Preparando los clips...")
        self._hide_error()

        self._thread = QThread(self)
        self._worker = PrepareWorker(self.session, force=False)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_prepare_progress)
        self._worker.finished.connect(self._on_prepared)
        self._thread.start()

    def _on_prepare_progress(self, done: int, total: int, message: str) -> None:
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.progress.setFormat(f"{message} - %p%")

    def _on_prepared(self, clips, error: str) -> None:  # noqa: ANN001
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
            self._thread = None
        self._worker = None
        self.progress.setVisible(False)
        self.start_button.setEnabled(True)

        if clips is None:
            self._show_error(error)
            self.statusBar().showMessage("No se pudo preparar.", 5000)
            return

        for warning in clips.warnings:
            self.statusBar().showMessage(warning, 8000)
        self.clip_info.setText(
            f"{clips.speaking.describe()}\n{clips.silent.describe()}\n"
            f"Caché total: {clips.total_mb:.0f} MB"
            + ("\n\nAvisos:\n- " + "\n- ".join(clips.warnings) if clips.warnings else "")
        )

        try:
            self.session.start()
        except SessionError as exc:
            self._show_error(str(exc))
            return
        self._set_running(True)
        self.statusBar().showMessage("En marcha.", 4000)

    def _on_stop(self) -> None:
        self.session.stop()
        self._set_running(False)
        self.preview.setText("Detenido.")
        self.preview.setPixmap(QPixmap())
        self._style_badge("-")
        self.state_badge.setText("Detenido")
        self.statusBar().showMessage("Detenido.", 4000)

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        # Lo que invalida la caché de fotogramas no se toca en caliente.
        for widget in (
            self.speaking_button,
            self.silent_button,
            self.resolution,
            self.fps_spin,
            self.fit_combo,
            self.mirror_check,
        ):
            widget.setEnabled(not running)

    # ------------------------------------------------------------------ #
    # Refresco periódico
    # ------------------------------------------------------------------ #

    def _refresh(self) -> None:
        status = self.session.status()
        voice, render = status.voice, status.render

        key = render.state.value if render.running else "-"
        self._style_badge(key)
        self.state_badge.setText(
            STATE_STYLES.get(key, ("Detenido", "", ""))[0]
            if render.running
            else "Detenido"
        )

        self.level_meter.setValue(int(max(-60.0, min(0.0, voice.rms_db))))
        if voice.running:
            detail = (
                f"Detector {voice.backend} a {voice.samplerate} Hz"
                + (f" (desde {voice.resampled_from} Hz)" if voice.resampled_from and voice.resampled_from != voice.samplerate else "")
                + f" | piso de ruido {voice.noise_floor_db:.0f} dB"
                + f" | SNR {voice.snr_db:+.0f} dB"
                + f" | latencia de activación ~{voice.expected_latency_ms} ms"
                + (" | calibrando..." if voice.calibrating else "")
            )
        else:
            detail = "Detector detenido."
        self.detector_info.setText(detail)

        if render.running:
            self.metrics_label.setText(
                f"FPS {render.fps_effective:5.2f}/{self.cfg.video.fps}   "
                f"ms/frame {render.avg_frame_ms:5.2f} (peor {render.worst_frame_ms:5.2f})   "
                f"fotogramas {render.frames_sent}   "
                f"tardíos {render.late_frames}   cambios {render.transitions}"
            )
            self.virtual_status.setText(
                f"Activa: {render.sink_device}" if render.virtual_output
                else "Cámara virtual inactiva."
            )
        else:
            self.metrics_label.setText("-")

        message = render.error or voice.hint or render.warning
        if message:
            self._show_error(message)
        elif self._last_error:
            self._hide_error()

        self._refresh_preview()

    def _refresh_preview(self) -> None:
        frame = self.session.latest_preview()
        if frame is None:
            return
        height, width = frame.shape[:2]
        image = QImage(
            np.ascontiguousarray(frame).data, width, height, 3 * width, QImage.Format_BGR888
        )
        pixmap = QPixmap.fromImage(image.copy())
        self.preview.setPixmap(
            pixmap.scaled(
                self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def _style_badge(self, key: str) -> None:
        _, background, color = STATE_STYLES.get(key, ("", "#23272e", "#8b949e"))
        self.state_badge.setStyleSheet(
            f"background:{background}; color:{color}; border-radius:6px; "
            "font-size:17px; font-weight:600;"
        )

    def _show_error(self, message: str) -> None:
        if message == self._last_error:
            return
        self._last_error = message
        self.error_banner.setText(message)
        self.error_banner.setVisible(True)

    def _hide_error(self) -> None:
        self._last_error = ""
        self.error_banner.setVisible(False)

    # ------------------------------------------------------------------ #

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.timer.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        self.session.close()
        super().closeEvent(event)


# --------------------------------------------------------------------------- #


def run_gui(cfg: AppConfig, config_file: Path | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Cámara virtual por voz")
    window = MainWindow(cfg, config_file)
    window.show()
    return int(app.exec())
