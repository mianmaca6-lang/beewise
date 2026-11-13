"""
Diagnóstico de la configuración de privacidad (solo lectura).

Devuelve QUÉ falla, nunca QUÉ valor tiene: los mensajes son fijos o describen
nombres de variables y rutas. No imprime ni registra secretos.
"""
from pathlib import Path

from . import cifrado, identidad

_VARIABLES_REQUERIDAS = ("COLMENA_CLAVE_CIFRADO", "COLMENA_SECRETO_SEUDONIMO")


def _nombres_en_env_file(ruta: Path) -> set[str]:
    """Solo nombres de claves del .env; los valores no se leen en claro ni se devuelven."""
    nombres: set[str] = set()
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        nombres.add(linea.split("=", 1)[0].strip())
    return nombres


def _revisar_archivo_env() -> list[str]:
    # pydantic-settings resuelve env_file=".env" contra el cwd del proceso, no contra la raíz del repo.
    ruta = Path.cwd() / ".env"
    if not ruta.is_file():
        return [
            f"No existe .env en el directorio de trabajo del proceso ({Path.cwd()}); "
            "las variables no se cargan. Arranca el servidor desde la carpeta que contiene .env."
        ]
    presentes = _nombres_en_env_file(ruta)
    problemas = []
    for v in _VARIABLES_REQUERIDAS:
        if v in presentes:
            continue
        pista = ""
        if v == "COLMENA_SECRETO_SEUDONIMO" and "COLMENA_HMAC_SECRET" in presentes:
            pista = " (el .env usa COLMENA_HMAC_SECRET; el código espera COLMENA_SECRETO_SEUDONIMO)"
        problemas.append(f"El .env no define {v}{pista}")
    return problemas


def estado_privacidad() -> dict:
    problemas: list[str] = []
    try:
        cifrado.cargar_clave()
    except cifrado.ErrorClave as exc:
        problemas.append(str(exc))
    try:
        identidad._secreto()
    except identidad.ErrorSecreto as exc:
        problemas.append(str(exc))
    problemas.extend(_revisar_archivo_env())
    return {"ok": not problemas, "problemas": problemas}
