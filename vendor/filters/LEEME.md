# Filtros de cámara virtual

Aquí van las DLL del filtro DirectShow que la aplicación registra para aparecer
como cámara en Windows. **El directorio se entrega vacío a propósito**: ver
`docs/CAMARA-VIRTUAL.md` para elegir entre las opciones y completarlo.

Se esperan dos archivos, y hacen falta los dos:

    UnityCaptureFilter64.dll   (o el filtro propio, x64)
    UnityCaptureFilter32.dll   (o el filtro propio, x86)

Los dos, porque un filtro DirectShow solo se carga en procesos de su misma
arquitectura: una aplicación de videollamada de 32 bits no verá un filtro de
64 bits.

El nombre se clasifica por arquitectura buscando «32» o «64» en él
(`camvoice/output/filters.py`, función `bundled_filters`).

Una vez puestas:

    camvoice-cli install-camera     # registra (pide permisos de administrador)
    camvoice-cli cameras            # comprueba que aparecen
    camvoice-cli install-camera --uninstall
