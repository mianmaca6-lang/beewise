"""
Logs estructurados (JSON por línea) con redacción de secretos.

Reglas:
- Nunca se registran montos, conceptos ni categorías personales: los módulos de
  negocio solo pasan identificadores internos y estados en `extra={"datos": {...}}`.
- Como red de seguridad, el filtro redacta llaves secretas Stellar (S...56),
  tokens en cabeceras y claves conocidas, aunque alguien las incluya por error.
"""
import json
import logging
import re
from datetime import datetime, timezone

_PATRONES = [
    (re.compile(r"\bS[A-Z2-7]{55}\b"), "S***REDACTADA***"),
    (re.compile(r"(?i)(x-api-token|x-tester-token|authorization)(\s*[:=]\s*)\S+"), r"\1\2***"),
    (re.compile(r"(?i)(token|secret|clave|password)(\"?\s*[:=]\s*\"?)[^\s\",}]+"), r"\1\2***"),
]

# Campos que jamás deben salir en un log aunque se pasen en `datos`.
_CAMPOS_PROHIBIDOS = {"monto", "concepto", "categoria", "descripcion", "contenido", "texto", "token", "clave"}


def redactar(texto: str) -> str:
    for patron, reemplazo in _PATRONES:
        texto = patron.sub(reemplazo, texto)
    return texto


def _limpiar_datos(datos):
    if isinstance(datos, dict):
        return {
            k: ("***" if k.lower() in _CAMPOS_PROHIBIDOS else _limpiar_datos(v)) for k, v in datos.items()
        }
    if isinstance(datos, (list, tuple)):
        return [_limpiar_datos(v) for v in datos]
    if isinstance(datos, str):
        return redactar(datos)
    return datos


class FormateadorJSON(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        salida = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "nivel": record.levelname,
            "logger": record.name,
            "mensaje": redactar(record.getMessage()),
        }
        datos = getattr(record, "datos", None)
        if datos:
            salida["datos"] = _limpiar_datos(datos)
        if record.exc_info:
            # Solo el tipo de excepción y su mensaje redactado; sin variables locales.
            tipo, valor, _ = record.exc_info
            salida["error"] = {"tipo": tipo.__name__ if tipo else None, "detalle": redactar(str(valor))}
        return json.dumps(salida, ensure_ascii=False, default=str)


_configurado = False


def configurar_logs(nivel: str = "INFO") -> None:
    global _configurado
    if _configurado:
        return
    manejador = logging.StreamHandler()
    manejador.setFormatter(FormateadorJSON())
    raiz = logging.getLogger("colmena")
    raiz.setLevel(nivel.upper())
    raiz.addHandler(manejador)
    raiz.propagate = False
    _configurado = True


def obtener_logger(nombre: str) -> logging.Logger:
    return logging.getLogger(f"colmena.{nombre}")
