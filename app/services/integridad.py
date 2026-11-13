"""
Representación canónica y hash de integridad de registros personales (D3).

ESPECIFICACIÓN v1 (no cambiar sin subir la versión):

1. Campos incluidos, y su normalización:
   v          entero 1 (versión del formato)
   ambito     "personal"
   seudonimo  64 hex minúsculas (32 bytes): liga la prueba a su dueño
   tipo       "gasto" | "ingreso" | "ahorro"
   monto      texto decimal con exactamente 2 decimales, sin separador de miles
              (Decimal, ROUND_HALF_EVEN NO aplica: se rechaza si trae >2 decimales)
   moneda     3 letras mayúsculas (ISO 4217)
   categoria  Unicode NFC, espacios colapsados, recortado, minúsculas
   concepto   Unicode NFC, espacios colapsados, recortado (conserva mayúsculas)
   fecha      UTC, formato "YYYY-MM-DDTHH:MM:SSZ" (sin fracciones de segundo)
   nonce      64 hex minúsculas: 32 bytes aleatorios (os.urandom) por registro

2. Serialización: JSON UTF-8 con claves ordenadas, sin espacios
   (separators=(",", ":")), ensure_ascii=False.

3. Hash: SHA-256( b"colmena/registro-personal/v1\\x00" || serializacion ).
   El prefijo separa dominios (evita reutilizar el hash en otro contexto).

Por qué el nonce: monto/categoría/concepto tienen poca entropía ("almuerzo", 15000).
Sin nonce, un tercero podría probar combinaciones y confirmar un registro contra el
hash público. Con 256 bits aleatorios eso es inviable. El nonce viaja DENTRO del
contenido cifrado: solo quien tiene la clave puede recomputar y verificar el hash.

La serialización canónica es, además, exactamente lo que se cifra y guarda.
"""
import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict

VERSION_FORMATO = 1
PREFIJO_DOMINIO = b"colmena/registro-personal/v1\x00"
TIPOS_VALIDOS = ("gasto", "ingreso", "ahorro")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MONEDA = re.compile(r"^[A-Z]{3}$")
MONTO_MAXIMO = Decimal("1000000000000")  # 10^12


class RegistroInvalido(ValueError):
    pass


def _texto(valor: str, minusculas: bool) -> str:
    t = " ".join(unicodedata.normalize("NFC", valor or "").split())
    return t.lower() if minusculas else t


def normalizar_monto(monto: Any) -> str:
    try:
        d = Decimal(str(monto))
    except (InvalidOperation, ValueError) as exc:
        raise RegistroInvalido("monto no es un número decimal") from exc
    if not d.is_finite() or d <= 0 or d > MONTO_MAXIMO:
        raise RegistroInvalido("monto fuera de rango")
    if d.as_tuple().exponent < -2 and d != d.quantize(Decimal("0.01")):
        raise RegistroInvalido("monto con más de 2 decimales")
    return str(d.quantize(Decimal("0.01")))


def normalizar_fecha(fecha: datetime) -> str:
    if fecha.tzinfo is None:
        raise RegistroInvalido("la fecha debe incluir zona horaria")
    return fecha.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def nuevo_nonce() -> str:
    return os.urandom(32).hex()


def construir_canonico(
    *, seudonimo: str, tipo: str, monto: Any, moneda: str, categoria: str, concepto: str, fecha: datetime, nonce: str
) -> Dict[str, Any]:
    if not _HEX64.match(seudonimo or ""):
        raise RegistroInvalido("seudónimo inválido")
    if not _HEX64.match(nonce or ""):
        raise RegistroInvalido("nonce inválido")
    tipo_n = _texto(tipo, True)
    if tipo_n not in TIPOS_VALIDOS:
        raise RegistroInvalido("tipo inválido")
    moneda_n = (moneda or "").strip().upper()
    if not _MONEDA.match(moneda_n):
        raise RegistroInvalido("moneda inválida")
    categoria_n = _texto(categoria, True)
    concepto_n = _texto(concepto, False)
    if not categoria_n or not concepto_n:
        raise RegistroInvalido("categoría y concepto son obligatorios")
    return {
        "v": VERSION_FORMATO,
        "ambito": "personal",
        "seudonimo": seudonimo,
        "tipo": tipo_n,
        "monto": normalizar_monto(monto),
        "moneda": moneda_n,
        "categoria": categoria_n,
        "concepto": concepto_n,
        "fecha": normalizar_fecha(fecha),
        "nonce": nonce,
    }


def serializar(canonico: Dict[str, Any]) -> bytes:
    return json.dumps(canonico, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def calcular_hash(serializado: bytes) -> bytes:
    return hashlib.sha256(PREFIJO_DOMINIO + serializado).digest()


def verificar(serializado: bytes, hash_esperado_hex: str) -> bool:
    return calcular_hash(serializado).hex() == (hash_esperado_hex or "").lower()
