"""Descubrimiento y registro de cámaras virtuales de Windows.

## Por qué este módulo existe

En Windows, una "cámara" que otras aplicaciones puedan seleccionar **no es un
proceso**: es un objeto COM que la aplicación receptora carga **dentro de su
propio proceso**. Teams no habla con nuestro programa; carga una DLL registrada
en el sistema y le pide fotogramas.

Eso impone un límite que no se puede sortear: **ninguna aplicación en Python
puede ser por sí misma una cámara de Windows.** Hace falta una DLL nativa
registrada en el sistema. Lo que sí puede hacer un programa en Python es
*alimentar* esa DLL por memoria compartida, que es lo que hace `pyvirtualcam`.

La pregunta, por tanto, no es "¿con DLL o sin DLL?" sino "**qué DLL y quién la
registra**":

| Opción | Quién registra | Nombre visible | Dependencia |
|---|---|---|---|
| Filtro de OBS | El instalador de OBS Studio | «OBS Virtual Camera» | Hay que instalar OBS |
| Filtro UnityCapture | Nuestro instalador | «Unity Video Capture» | Ninguna |
| Filtro propio | Nuestro instalador | el que queramos | Ninguna (hay que compilarlo) |

Este módulo hace la aplicación **independiente del origen del filtro**:
enumera lo que hay registrado en el sistema, elige el mejor backend disponible
y sabe registrar y quitar un filtro propio sin depender de OBS.

## Detalle que importa: 32 frente a 64 bits

Un filtro DirectShow solo se puede cargar en un proceso de su misma
arquitectura. Una aplicación de videollamada de 32 bits **únicamente** verá un
filtro de 32 bits. Por eso OBS registra dos DLL, y por eso aquí se consultan
las dos vistas del registro y se informa de cuál falta.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "VIDEO_INPUT_CATEGORY",
    "RegisteredCamera",
    "list_directshow_cameras",
    "detect_backends",
    "bundled_filters",
    "register_filter",
    "unregister_filter",
    "FilterError",
]

VIDEO_INPUT_CATEGORY = "{860BB310-5D01-11D0-BD3B-00A0C911CE86}"
"""CLSID_VideoInputDeviceCategory: donde Windows lista las fuentes de video."""

_OBS_CLSID = "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}"

# Pistas para deducir qué backend de pyvirtualcam alimenta cada filtro, por
# nombre amistoso en minúsculas. El CLSID de OBS es estable y se comprueba
# aparte; UnityCapture permite registrar varios dispositivos con CLSID
# distintos, así que ahí se reconoce por el nombre.
_BACKEND_HINTS = (
    ("obs virtual camera", "obs"),
    ("unity video capture", "unitycapture"),
    ("camvoice", "unitycapture"),  # filtro propio derivado de UnityCapture
)


class FilterError(RuntimeError):
    """Fallo al registrar o quitar un filtro, con mensaje para el usuario."""


@dataclass(frozen=True, slots=True)
class RegisteredCamera:
    friendly_name: str
    clsid: str
    dll_path: str
    architecture: str
    """'64-bit' o '32-bit', según la vista del registro donde apareció."""

    @property
    def dll_exists(self) -> bool:
        return bool(self.dll_path) and Path(self.dll_path).exists()

    @property
    def backend(self) -> str:
        """Backend de pyvirtualcam que puede alimentar este filtro, o ''."""
        if self.clsid.upper() == _OBS_CLSID:
            return "obs"
        low = self.friendly_name.lower()
        for hint, backend in _BACKEND_HINTS:
            if hint in low:
                return backend
        return ""

    @property
    def label(self) -> str:
        state = "" if self.dll_exists else "  [DLL AUSENTE: registro huérfano]"
        backend = f"  backend={self.backend}" if self.backend else "  backend=desconocido"
        return f"{self.friendly_name} ({self.architecture}){backend}{state}"


# --------------------------------------------------------------------------- #
# Enumeración
# --------------------------------------------------------------------------- #


def list_directshow_cameras() -> list[RegisteredCamera]:
    """Cámaras DirectShow registradas en el sistema, en las dos vistas.

    Incluye las cámaras físicas: Windows no distingue entre una webcam y un
    filtro virtual, y de hecho esa indistinguibilidad es justo lo que hace que
    el truco funcione en las aplicaciones de videollamada.
    """
    if sys.platform != "win32":
        return []
    import winreg

    found: list[RegisteredCamera] = []
    views = (
        (winreg.KEY_WOW64_64KEY, "64-bit"),
        (winreg.KEY_WOW64_32KEY, "32-bit"),
    )
    for access_flag, architecture in views:
        key_path = rf"SOFTWARE\Classes\CLSID\{VIDEO_INPUT_CATEGORY}\Instance"
        try:
            root = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | access_flag
            )
        except OSError:
            continue
        with root:
            for index in range(_subkey_count(root)):
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                found.append(_read_instance(root, name, access_flag, architecture))
    return [c for c in found if c is not None]


def _subkey_count(key) -> int:  # noqa: ANN001
    import winreg

    try:
        return winreg.QueryInfoKey(key)[0]
    except OSError:
        return 0


def _read_instance(root, name: str, access_flag: int, architecture: str):  # noqa: ANN001
    import winreg

    try:
        with winreg.OpenKey(root, name, 0, winreg.KEY_READ | access_flag) as entry:
            friendly = _value(entry, "FriendlyName") or name
            clsid = _value(entry, "CLSID") or name
    except OSError:
        return None

    dll = ""
    try:
        server = rf"SOFTWARE\Classes\CLSID\{clsid}\InprocServer32"
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, server, 0, winreg.KEY_READ | access_flag
        ) as key:
            dll = _value(key, "") or ""
    except OSError:
        pass

    return RegisteredCamera(str(friendly), str(clsid).upper(), str(dll), architecture)


def _value(key, name: str):  # noqa: ANN001
    import winreg

    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Selección de backend
# --------------------------------------------------------------------------- #


def detect_backends(*, prefer_independent: bool = True) -> list[str]:
    """Backends de pyvirtualcam utilizables, el preferido primero.

    Con `prefer_independent` se coloca delante un filtro propio o de
    UnityCapture, de modo que una instalación autónoma no quede supeditada a
    que OBS esté presente ni compita con él por el dispositivo.
    """
    available: list[str] = []
    for camera in list_directshow_cameras():
        backend = camera.backend
        if backend and camera.dll_exists and backend not in available:
            available.append(backend)
    if prefer_independent:
        available.sort(key=lambda b: 1 if b == "obs" else 0)
    return available


def describe_availability() -> str:
    """Resumen legible de la situación de la cámara virtual en este equipo."""
    cameras = list_directshow_cameras()
    virtual = [c for c in cameras if c.backend]
    if not virtual:
        return (
            "No hay ninguna cámara virtual registrada en el sistema. Ejecuta "
            "«camvoice-cli install-camera» para registrar la que acompaña a la "
            "aplicación."
        )
    lines = ["Cámaras virtuales registradas:"]
    lines += [f"  - {c.label}" for c in virtual]

    arches = {c.architecture for c in virtual}
    if "32-bit" not in arches:
        lines.append(
            "  Aviso: solo hay filtro de 64 bits. Una aplicación de "
            "videollamada de 32 bits no verá la cámara."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #


def filters_dir() -> Path:
    """Dónde se buscan las DLL de filtro que acompañan a la aplicación.

    Funciona tanto ejecutando desde el código fuente como desde el ejecutable
    de PyInstaller, que extrae los datos a `sys._MEIPASS`.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "filters"
    return Path(__file__).resolve().parent.parent.parent / "vendor" / "filters"


def bundled_filters() -> dict[str, Path]:
    """DLL de filtro disponibles para registrar, por arquitectura."""
    directory = filters_dir()
    out: dict[str, Path] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.dll")):
        name = path.name.lower()
        if "32" in name and "64" not in name:
            out.setdefault("32-bit", path)
        else:
            out.setdefault("64-bit", path)
    return out


DEFAULT_CAMERA_NAME = "CamVoice Virtual Camera"
"""Nombre con el que aparece la cámara en Teams, Zoom, Meet y el navegador.

El filtro de UnityCapture acepta el nombre como parámetro de registro
(`regsvr32 /i:UnityCaptureName=...`), así que se puede poner un nombre propio
**sin recompilar nada**. Era la única ventaja real de mantener un filtro
compilado a medida, y desaparece.
"""


def register_filter(
    dll: Path | None = None,
    *,
    unregister: bool = False,
    camera_name: str = DEFAULT_CAMERA_NAME,
) -> str:
    """Registra (o quita) un filtro DirectShow con `regsvr32`, elevando.

    Requiere permisos de administrador, porque escribe en HKLM. Se solicita la
    elevación con ShellExecute «runas», que muestra el diálogo de UAC: es
    preferible a fallar con «acceso denegado» y dejar que el usuario adivine.

    Se registran siempre **las dos arquitecturas** cuando están disponibles: un
    filtro DirectShow solo se carga en procesos de su misma arquitectura, y hay
    aplicaciones de videollamada de 32 bits.

    Devuelve un mensaje describiendo lo hecho. Levanta `FilterError` si no hay
    nada que registrar o si no se consigue la elevación.
    """
    if sys.platform != "win32":
        raise FilterError("El registro de filtros DirectShow solo existe en Windows.")

    targets = [dll] if dll is not None else list(bundled_filters().values())
    targets = [Path(t) for t in targets if t]
    missing = [t for t in targets if not t.exists()]
    if not targets or missing:
        raise FilterError(
            "No se encontró ninguna DLL de filtro para registrar.\n"
            f"Se buscaba en: {filters_dir()}\n\n"
            "Esta copia de la aplicación se distribuyó sin el filtro de cámara "
            "virtual. Ver docs/CAMARA-VIRTUAL.md para obtenerlo, o instala OBS "
            "Studio para usar su filtro."
        )

    verb = "quitado" if unregister else "registrado"
    done: list[str] = []
    for target in targets:
        arguments = _regsvr32_arguments(target, unregister, camera_name)
        code = _run_elevated("regsvr32.exe", arguments)
        if code <= 32:
            raise FilterError(
                f"No se pudo elevar para {('quitar' if unregister else 'registrar')} "
                f"«{target.name}» (código {code}). Hay que aceptar el aviso de "
                "Control de cuentas de usuario."
            )
        done.append(target.name)

    suffix = "" if unregister else f" como «{camera_name}»"
    return f"Filtro {verb}{suffix}: {', '.join(done)}"


def _regsvr32_arguments(target: Path, unregister: bool, camera_name: str) -> str:
    """Línea de órdenes de regsvr32 para esta DLL.

    El nombre personalizado se pasa en `/i:`, que regsvr32 entrega a
    `DllInstall` del filtro. Solo lo entiende el filtro de UnityCapture y sus
    derivados; para cualquier otro se omite, porque un `/i` inesperado hace
    fallar el registro.
    """
    if unregister:
        return f'/u /s "{target}"'
    if camera_name and _supports_custom_name(target):
        # Mismo orden y entrecomillado que el Install.bat oficial del filtro.
        # Dos detalles que no son opcionales:
        #   - el token "/i:..." va entre comillas, porque el nombre lleva
        #     espacios y si no regsvr32 lo corta por el primero;
        #   - NO se usa "/n": eso saltaría DllRegisterServer y el filtro
        #     quedaría a medio registrar, sin aparecer como cámara.
        return f'/s "{target}" "/i:UnityCaptureName={camera_name}"'
    return f'/s "{target}"'


def _supports_custom_name(target: Path) -> bool:
    return "unitycapture" in target.name.lower().replace(" ", "")


def unregister_filter(dll: Path | None = None) -> str:
    return register_filter(dll, unregister=True)


def _run_elevated(executable: str, arguments: str) -> int:
    """ShellExecuteW con el verbo «runas». Devuelve el código de ShellExecute."""
    import ctypes

    SW_HIDE = 0
    try:
        return int(
            ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, "runas", executable, arguments, None, SW_HIDE
            )
        )
    except Exception as exc:  # pragma: no cover - defensivo
        raise FilterError(f"No se pudo invocar {executable}: {exc}") from exc


def obs_install_script() -> Path | None:
    """Ruta al registrador del filtro de OBS, si OBS está instalado.

    Camino alternativo para quien ya tenga OBS pero con el filtro sin registrar.
    """
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "obs-studio" / "data" / "obs-plugins" / "win-dshow" / "virtualcam-install.bat",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None
