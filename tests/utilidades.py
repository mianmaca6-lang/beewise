import uuid
from typing import List, Optional, Tuple

from app.services.soroban_libro import ErrorSoroban, PruebaOnChain, ResultadoRegistro


class PublicadorFalso:
    """Registra exactamente lo que se enviaría a Soroban."""

    def __init__(self, error: Optional[Exception] = None, ya_existia: bool = False):
        self.llamadas: List[Tuple[bytes, ...]] = []
        self.error = error
        self.ya_existia = ya_existia
        self.cadena = {}

    def registrar(self, *args, **kwargs) -> ResultadoRegistro:
        assert not kwargs, "el publicador no debe recibir argumentos con nombre"
        self.llamadas.append(tuple(args))
        if self.error:
            raise self.error
        seudonimo, h = args
        self.cadena[bytes(h)] = PruebaOnChain(seudonimo=bytes(seudonimo), ledger=100 + len(self.cadena))
        return ResultadoRegistro(tx_hash="ab" * 32, ledger=self.cadena[bytes(h)].ledger, ya_existia=self.ya_existia)

    def obtener(self, h):
        return self.cadena.get(bytes(h))


def cuerpo(id_externo="5550001", **cambios):
    base = {
        "ambito": "personal",
        "usuario": {"canal": "telegram", "id_externo": id_externo},
        "tipo": "gasto",
        "monto": "98765.43",
        "moneda": "COP",
        "categoria": "Comida",
        "concepto": "ConceptoSecretoXYZ almuerzo",
        "fecha": "2026-11-12T12:30:00-05:00",
    }
    base.update(cambios)
    return base


def cabeceras(token, clave=None):
    return {"X-Api-Token": token, "Idempotency-Key": clave or f"idem-{uuid.uuid4().hex}"}


def error_soroban(codigo="rpc_ConnectionError", reintentable=True, tx_hash=None):
    return ErrorSoroban(codigo, reintentable, tx_hash)
