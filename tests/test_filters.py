"""Pruebas del descubrimiento y registro de filtros de cámara virtual.

No registran ni quitan nada del sistema: eso requiere permisos de
administrador y modificaría la máquina. Lo que se verifica es la lectura del
registro, la clasificación de lo encontrado y que los caminos de error den
mensajes accionables.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from camvoice.output.filters import (
    FilterError,
    RegisteredCamera,
    bundled_filters,
    describe_availability,
    detect_backends,
    filters_dir,
    list_directshow_cameras,
    register_filter,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")


# --------------------------------------------------------------------------- #
# Clasificación
# --------------------------------------------------------------------------- #


def test_reconoce_el_filtro_de_obs_por_su_clsid():
    cam = RegisteredCamera(
        "Lo que sea", "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}", "x.dll", "64-bit"
    )
    assert cam.backend == "obs"


def test_reconoce_unitycapture_por_el_nombre():
    cam = RegisteredCamera("Unity Video Capture", "{0000}", "x.dll", "64-bit")
    assert cam.backend == "unitycapture"


def test_un_filtro_propio_usa_el_backend_de_unitycapture():
    """El filtro propio deriva de UnityCapture, así que habla su protocolo."""
    cam = RegisteredCamera("CamVoice Virtual Camera", "{0001}", "x.dll", "64-bit")
    assert cam.backend == "unitycapture"


def test_una_camara_fisica_no_declara_backend():
    cam = RegisteredCamera("Integrated Webcam", "{0002}", "usbvideo.dll", "64-bit")
    assert cam.backend == ""


def test_detecta_un_registro_huerfano(tmp_path):
    """Una DLL que ya no existe deja la entrada del registro pero no sirve."""
    cam = RegisteredCamera("Unity Video Capture", "{0003}", str(tmp_path / "no.dll"), "64-bit")
    assert not cam.dll_exists
    assert "AUSENTE" in cam.label


def test_una_dll_presente_se_marca_como_disponible(tmp_path):
    dll = tmp_path / "filtro.dll"
    dll.write_bytes(b"MZ")
    cam = RegisteredCamera("Unity Video Capture", "{0004}", str(dll), "64-bit")
    assert cam.dll_exists
    assert "AUSENTE" not in cam.label


# --------------------------------------------------------------------------- #
# Enumeración real
# --------------------------------------------------------------------------- #


@windows_only
def test_la_enumeracion_del_registro_funciona():
    cameras = list_directshow_cameras()
    assert isinstance(cameras, list)
    for cam in cameras:
        assert cam.clsid.startswith("{")
        assert cam.architecture in ("64-bit", "32-bit")


@windows_only
def test_los_backends_detectados_son_conocidos():
    for backend in detect_backends():
        assert backend in ("obs", "unitycapture")


@windows_only
def test_se_prefiere_un_filtro_independiente_de_obs():
    """Con varios filtros, OBS va el último: no debe competir con él."""
    backends = detect_backends(prefer_independent=True)
    if "obs" in backends and len(backends) > 1:
        assert backends[-1] == "obs"


@windows_only
def test_el_resumen_es_legible():
    text = describe_availability()
    assert text
    assert "install-camera" in text or "registradas" in text


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #


def test_el_directorio_de_filtros_esta_definido():
    assert isinstance(filters_dir(), Path)


def test_registrar_sin_dll_da_un_mensaje_accionable(monkeypatch, tmp_path):
    """CA-12: el error dice dónde se buscó y qué hacer, no solo que falló."""
    monkeypatch.setattr("camvoice.output.filters.filters_dir", lambda: tmp_path)
    with pytest.raises(FilterError) as info:
        register_filter()
    message = str(info.value)
    assert str(tmp_path) in message
    assert "OBS" in message or "CAMARA-VIRTUAL" in message


def test_registrar_una_dll_inexistente_tambien_falla_limpio(tmp_path):
    with pytest.raises(FilterError):
        register_filter(tmp_path / "no-existe.dll")


def test_las_dll_incluidas_se_clasifican_por_arquitectura(monkeypatch, tmp_path):
    (tmp_path / "FiltroCamvoice64.dll").write_bytes(b"MZ")
    (tmp_path / "FiltroCamvoice32.dll").write_bytes(b"MZ")
    monkeypatch.setattr("camvoice.output.filters.filters_dir", lambda: tmp_path)
    found = bundled_filters()
    assert set(found) == {"64-bit", "32-bit"}
    assert "64" in found["64-bit"].name
    assert "32" in found["32-bit"].name


# --------------------------------------------------------------------------- #
# Línea de órdenes de regsvr32
# --------------------------------------------------------------------------- #


def test_el_nombre_personalizado_va_entre_comillas():
    """El nombre lleva espacios; sin comillas regsvr32 lo corta por el primero."""
    from camvoice.output.filters import _regsvr32_arguments

    args = _regsvr32_arguments(Path("C:/x/UnityCaptureFilter64.dll"), False, "Mi Camara")
    assert '"/i:UnityCaptureName=Mi Camara"' in args


def test_el_registro_no_usa_la_bandera_n():
    """`/n` saltaría DllRegisterServer y el filtro no aparecería como cámara."""
    from camvoice.output.filters import _regsvr32_arguments

    args = _regsvr32_arguments(Path("C:/x/UnityCaptureFilter64.dll"), False, "Mi Camara")
    assert " /n" not in args


def test_la_ruta_de_la_dll_va_entre_comillas():
    """Program Files lleva espacios: sin comillas regsvr32 no halla la DLL."""
    from camvoice.output.filters import _regsvr32_arguments

    dll = Path("C:/Archivos de programa/x/UnityCaptureFilter64.dll")
    args = _regsvr32_arguments(dll, False, "N")
    assert f'"{dll}"' in args


def test_quitar_el_registro_no_pasa_el_nombre():
    """DllInstall no se invoca al desregistrar; pasar /i haría fallar regsvr32."""
    from camvoice.output.filters import _regsvr32_arguments

    args = _regsvr32_arguments(Path("C:/x/UnityCaptureFilter64.dll"), True, "Mi Camara")
    assert "/u" in args
    assert "UnityCaptureName" not in args


def test_un_filtro_ajeno_no_recibe_el_parametro_de_nombre():
    """Solo UnityCapture y derivados entienden /i:; un /i inesperado falla."""
    from camvoice.output.filters import _regsvr32_arguments

    args = _regsvr32_arguments(Path("C:/x/obs-virtualcam-module64.dll"), False, "Mi Camara")
    assert "UnityCaptureName" not in args


def test_las_dll_incluidas_existen_y_son_binarios_pe():
    """Las DLL del filtro deben venir con el proyecto y ser ejecutables válidos."""
    found = bundled_filters()
    if not found:
        pytest.skip("este árbol no incluye las DLL del filtro")
    assert set(found) == {"64-bit", "32-bit"}
    for path in found.values():
        assert path.read_bytes()[:2] == b"MZ", f"{path.name} no es un binario PE"
