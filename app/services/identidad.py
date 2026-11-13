"""
Identificadores seudónimos.

- id_externo_hmac: HMAC-SHA256(COLMENA_SECRETO_SEUDONIMO, "canal:id_externo"). Permite
  reconocer al usuario de Telegram sin guardar su id en claro. Un HMAC (no un SHA-256
  simple) impide recuperar ids de Telegram por fuerza bruta: son números cortos.
- seudonimo: 32 bytes ALEATORIOS por usuario. No deriva de ningún dato personal, así que
  aunque se filtre el secreto no puede asociarse a una persona. Es lo único de identidad
  que llega a Soroban.
- huella_solicitud: HMAC de la solicitud canónica (sin nonce) para detectar que una
  clave de idempotencia se reutilizó con otro contenido. Es HMAC para que la columna no
  permita confirmar montos/conceptos por diccionario.
"""
import hashlib
import hmac
import os

from ..config import obtener_config


class ErrorSecreto(RuntimeError):
    pass


def _secreto() -> bytes:
    s = obtener_config().colmena_secreto_seudonimo
    if s is None or len(s.get_secret_value()) < 32:
        raise ErrorSecreto("COLMENA_SECRETO_SEUDONIMO falta o tiene menos de 32 caracteres")
    return s.get_secret_value().encode("utf-8")


def _hmac(etiqueta: str, datos: bytes) -> str:
    return hmac.new(_secreto(), etiqueta.encode() + b"\x00" + datos, hashlib.sha256).hexdigest()


def hmac_id_externo(canal: str, id_externo: str) -> str:
    return _hmac("id_externo/v1", f"{canal}:{id_externo.strip()}".encode("utf-8"))


def nuevo_seudonimo() -> str:
    return os.urandom(32).hex()


def huella_solicitud(serializado_sin_nonce: bytes) -> str:
    return _hmac("solicitud/v1", serializado_sin_nonce)
