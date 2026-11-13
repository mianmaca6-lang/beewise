"""
Respaldo y restauración de la base SQLite.

- Usa la API de backup de sqlite3: copia consistente aunque la BD esté en modo WAL
  y otros procesos la estén usando (copiar el archivo a mano NO es seguro con WAL).
- Cada respaldo genera un manifiesto JSON con fecha, origen, tamaño, SHA-256 y el
  resultado de `PRAGMA integrity_check`.
- Nunca sobrescribe ni borra respaldos existentes.
- Restaurar exige verificar el SHA-256 y la integridad, y antes de reemplazar la
  BD destino guarda una copia de ella ("pre-restauracion").

IMPORTANTE: los registros personales están cifrados. Un respaldo sin la clave
COLMENA_CLAVE_CIFRADO (y su versión) no permite leerlos. Guarda la clave por
separado del respaldo (ver docs/OPERACION.md).
"""
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


class RespaldoError(Exception):
    pass


@dataclass(frozen=True)
class ResultadoRespaldo:
    archivo: str
    manifiesto: str
    sha256: str
    bytes: int


def _sha256_archivo(ruta: str) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _integridad(ruta: str) -> str:
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        return con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()


def respaldar(ruta_bd: str, carpeta_destino: str, etiqueta: str = "manual") -> ResultadoRespaldo:
    if not os.path.isfile(ruta_bd):
        raise RespaldoError(f"No existe la base de datos: {ruta_bd}")
    os.makedirs(carpeta_destino, exist_ok=True)

    marca = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base = os.path.splitext(os.path.basename(ruta_bd))[0]
    destino = os.path.join(carpeta_destino, f"{base}_{marca}_{etiqueta}.db")
    if os.path.exists(destino):
        raise RespaldoError(f"El respaldo ya existe, no se sobrescribe: {destino}")

    origen = sqlite3.connect(ruta_bd)
    copia = sqlite3.connect(destino)
    try:
        origen.backup(copia)
    finally:
        copia.close()
        origen.close()

    integridad = _integridad(destino)
    if integridad != "ok":
        raise RespaldoError(f"El respaldo no pasó integrity_check: {integridad}")

    sha = _sha256_archivo(destino)
    tam = os.path.getsize(destino)
    manifiesto = destino + ".json"
    with open(manifiesto, "w", encoding="utf-8") as f:
        json.dump(
            {
                "creado_utc": datetime.now(timezone.utc).isoformat(),
                "origen": os.path.abspath(ruta_bd),
                "archivo": os.path.basename(destino),
                "bytes": tam,
                "sha256": sha,
                "integrity_check": integridad,
                "etiqueta": etiqueta,
                "nota": "Los registros personales requieren COLMENA_CLAVE_CIFRADO para leerse.",
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return ResultadoRespaldo(archivo=destino, manifiesto=manifiesto, sha256=sha, bytes=tam)


def verificar_respaldo(archivo: str) -> dict:
    manifiesto = archivo + ".json"
    if not os.path.isfile(archivo) or not os.path.isfile(manifiesto):
        raise RespaldoError("Falta el respaldo o su manifiesto")
    with open(manifiesto, encoding="utf-8") as f:
        datos = json.load(f)
    if _sha256_archivo(archivo) != datos["sha256"]:
        raise RespaldoError("El SHA-256 del respaldo no coincide con el manifiesto")
    integridad = _integridad(archivo)
    if integridad != "ok":
        raise RespaldoError(f"El respaldo no pasó integrity_check: {integridad}")
    return datos


def restaurar(archivo: str, ruta_bd: str, carpeta_respaldos: Optional[str] = None) -> Optional[str]:
    """
    Restaura `archivo` sobre `ruta_bd`. Si `ruta_bd` existe, primero la respalda
    (etiqueta 'pre-restauracion') y devuelve la ruta de esa copia.
    Detén la API y los workers antes de restaurar.
    """
    verificar_respaldo(archivo)
    copia_previa = None
    if os.path.exists(ruta_bd):
        carpeta = carpeta_respaldos or os.path.dirname(os.path.abspath(archivo))
        copia_previa = respaldar(ruta_bd, carpeta, etiqueta="pre-restauracion").archivo

    os.makedirs(os.path.dirname(os.path.abspath(ruta_bd)), exist_ok=True)
    origen = sqlite3.connect(f"file:{archivo}?mode=ro", uri=True)
    destino = sqlite3.connect(ruta_bd)
    try:
        origen.backup(destino)
    finally:
        destino.close()
        origen.close()

    integridad = _integridad(ruta_bd)
    if integridad != "ok":
        raise RespaldoError(f"La BD restaurada no pasó integrity_check: {integridad}")
    return copia_previa
