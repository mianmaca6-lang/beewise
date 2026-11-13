"""
Reglas de votación de socios (D7) — REFERENCIA DE DISEÑO.

Estado: implementada y probada solo como función pura en Python. El contrato Soroban
de socios (Tesoreria) que hará cumplir estas reglas on-chain es de una fase posterior;
ver docs/ARQUITECTURA_SOCIOS.md. Ningún endpoint usa esto todavía.

  Unanimidad      → n votos
  MayoriaSimple   → n // 2 + 1  (floor(n/2) + 1)

  n=2 → 2 y 2 | n=3 → 3 y 2 | n=4 → 4 y 3 | n=5 → 5 y 3
"""
from enum import Enum


class Modalidad(str, Enum):
    UNANIMIDAD = "unanimidad"
    MAYORIA_SIMPLE = "mayoria_simple"


def votos_requeridos(socios_habilitados: int, modalidad: Modalidad) -> int:
    if socios_habilitados < 1:
        raise ValueError("debe haber al menos un socio habilitado")
    if modalidad == Modalidad.UNANIMIDAD:
        return socios_habilitados
    if modalidad == Modalidad.MAYORIA_SIMPLE:
        return socios_habilitados // 2 + 1
    raise ValueError("modalidad desconocida")
