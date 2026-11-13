"""
Cifrado autenticado de datos personales (AES-256-GCM, biblioteca `cryptography`).

- La clave (32 bytes, base64) viene de COLMENA_CLAVE_CIFRADO; nunca se guarda en la BD.
- Formato del blob: nonce (12 bytes aleatorios) || ciphertext || tag (16 bytes).
- AAD (datos asociados): liga el blob a su contexto (p. ej. el usuario dueño). Si alguien
  mueve un blob a la fila de otro usuario, descifrar falla.
- Clave incorrecta o blob alterado → ErrorDescifrado (GCM verifica autenticidad).

PÉRDIDA DE LA CLAVE = PÉRDIDA DEFINITIVA DE ACCESO A LOS REGISTROS PERSONALES.
"""
import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import obtener_config

_TAM_NONCE = 12
_TAM_CLAVE = 32


class ErrorClave(RuntimeError):
    """La clave de cifrado falta o es inválida."""


class ErrorDescifrado(Exception):
    """Clave incorrecta, AAD distinto o contenido alterado."""


@dataclass(frozen=True)
class ClaveCifrado:
    version: int
    _material: bytes

    def __repr__(self) -> str:  # nunca mostrar el material
        return f"ClaveCifrado(version={self.version}, material=***)"


def clave_desde_base64(valor: str, version: int) -> ClaveCifrado:
    try:
        material = base64.b64decode(valor, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ErrorClave("La clave de cifrado no es base64 válido") from exc
    if len(material) != _TAM_CLAVE:
        raise ErrorClave("La clave de cifrado debe tener exactamente 32 bytes (AES-256)")
    return ClaveCifrado(version=version, _material=material)


def cargar_clave() -> ClaveCifrado:
    cfg = obtener_config()
    if cfg.colmena_clave_cifrado is None or not cfg.colmena_clave_cifrado.get_secret_value():
        raise ErrorClave("COLMENA_CLAVE_CIFRADO no está configurada")
    return clave_desde_base64(cfg.colmena_clave_cifrado.get_secret_value(), cfg.colmena_clave_cifrado_version)


def generar_clave_base64() -> str:
    """Para el procedimiento de instalación: genera una clave nueva."""
    return base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()


def cifrar(texto_plano: bytes, aad: bytes, clave: ClaveCifrado) -> bytes:
    nonce = os.urandom(_TAM_NONCE)
    return nonce + AESGCM(clave._material).encrypt(nonce, texto_plano, aad)


def descifrar(blob: bytes, aad: bytes, clave: ClaveCifrado) -> bytes:
    if len(blob) < _TAM_NONCE + 16:
        raise ErrorDescifrado("Contenido cifrado demasiado corto")
    nonce, cuerpo = blob[:_TAM_NONCE], blob[_TAM_NONCE:]
    try:
        return AESGCM(clave._material).decrypt(nonce, cuerpo, aad)
    except InvalidTag as exc:
        raise ErrorDescifrado("No se pudo descifrar: clave incorrecta o contenido alterado") from exc
