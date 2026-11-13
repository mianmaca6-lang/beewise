"""
Comunicación con el contrato LibroPersonal. Separada de la lógica de negocio.

La interfaz solo acepta (seudonimo: 32 bytes, hash: 32 bytes). No existe forma de
pasarle montos, categorías ni conceptos: `_exigir_32_bytes` lo hace cumplir en tiempo
de ejecución y el contrato lo hace cumplir en su especificación.

Solo FastAPI usa este módulo. n8n nunca firma ni habla con Soroban.

Implementaciones:
- PublicadorNoConfigurado: si falta el contrato o la llave. Falla de forma explícita y
  reintentable (el registro queda en 'error_reintentable', nunca se pierde en silencio).
- PublicadorSoroban: usa Stellar RPC (simular → firmar → enviar → confirmar).
"""
import re
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from ..config import obtener_config
from ..logging_seguro import obtener_logger

log = obtener_logger("soroban")

CODIGO_YA_REGISTRADO = 1  # Error::YaRegistrado en contratos/libro_personal/src/lib.rs
_ERROR_CONTRATO = re.compile(r"Error\(Contract, #(\d+)\)")


class ErrorSoroban(Exception):
    def __init__(self, codigo: str, reintentable: bool, tx_hash: Optional[str] = None):
        super().__init__(codigo)
        self.codigo = codigo[:60]
        self.reintentable = reintentable
        self.tx_hash = tx_hash


@dataclass(frozen=True)
class ResultadoRegistro:
    tx_hash: Optional[str]
    ledger: Optional[int]
    ya_existia: bool = False


@dataclass(frozen=True)
class PruebaOnChain:
    seudonimo: bytes
    ledger: int


class PublicadorLibroPersonal(Protocol):
    def registrar(self, seudonimo: bytes, hash_integridad: bytes) -> ResultadoRegistro: ...
    def obtener(self, hash_integridad: bytes) -> Optional[PruebaOnChain]: ...


def _exigir_32_bytes(nombre: str, valor: bytes) -> None:
    if not isinstance(valor, (bytes, bytearray)) or len(valor) != 32:
        raise TypeError(f"{nombre} debe ser exactamente 32 bytes")


class PublicadorNoConfigurado:
    def registrar(self, seudonimo: bytes, hash_integridad: bytes) -> ResultadoRegistro:
        _exigir_32_bytes("seudonimo", seudonimo)
        _exigir_32_bytes("hash_integridad", hash_integridad)
        raise ErrorSoroban("soroban_no_configurado", reintentable=True)

    def obtener(self, hash_integridad: bytes) -> Optional[PruebaOnChain]:
        raise ErrorSoroban("soroban_no_configurado", reintentable=True)


def codigo_error_contrato(texto: str) -> Optional[int]:
    m = _ERROR_CONTRATO.search(texto or "")
    return int(m.group(1)) if m else None


class PublicadorSoroban:
    """IMPLEMENTADO, NO PROBADO CONTRA LA RED (ver docs/INFORME_FASE_0_1.md)."""

    def __init__(self, rpc_url: str, red: str, contrato_id: str, firmante, timeout_s: int = 60):
        from stellar_sdk import Network, SorobanServer

        self._servidor = SorobanServer(rpc_url)
        self._passphrase = Network.PUBLIC_NETWORK_PASSPHRASE if red == "public" else Network.TESTNET_NETWORK_PASSPHRASE
        self._contrato = contrato_id
        self._firmante = firmante  # Keypair; nunca se registra ni se expone
        self._timeout = timeout_s

    def __repr__(self) -> str:
        return f"PublicadorSoroban(contrato={self._contrato}, firmante=***)"

    def _tx(self, funcion: str, parametros):
        from stellar_sdk import TransactionBuilder

        cuenta = self._servidor.load_account(self._firmante.public_key)
        return (
            TransactionBuilder(cuenta, self._passphrase, base_fee=100)
            .append_invoke_contract_function_op(self._contrato, funcion, parametros)
            .set_timeout(self._timeout)
            .build()
        )

    def registrar(self, seudonimo: bytes, hash_integridad: bytes) -> ResultadoRegistro:
        _exigir_32_bytes("seudonimo", seudonimo)
        _exigir_32_bytes("hash_integridad", hash_integridad)
        from stellar_sdk import scval
        from stellar_sdk.exceptions import PrepareTransactionException
        from stellar_sdk.soroban_rpc import GetTransactionStatus, SendTransactionStatus

        try:
            tx = self._tx("registrar", [scval.to_bytes(bytes(seudonimo)), scval.to_bytes(bytes(hash_integridad))])
            try:
                tx = self._servidor.prepare_transaction(tx)
            except PrepareTransactionException as exc:
                codigo = codigo_error_contrato(str(exc.simulate_transaction_response.error))
                if codigo == CODIGO_YA_REGISTRADO:
                    return self._confirmar_existente(seudonimo, hash_integridad)
                raise ErrorSoroban(f"simulacion_contrato_{codigo}" if codigo else "simulacion_fallida", False)
            tx.sign(self._firmante)
            envio = self._servidor.send_transaction(tx)
            if envio.status == SendTransactionStatus.ERROR:
                raise ErrorSoroban("envio_rechazado", False, envio.hash)
            if envio.status == SendTransactionStatus.TRY_AGAIN_LATER:
                raise ErrorSoroban("rpc_ocupado", True, envio.hash)

            limite = time.monotonic() + self._timeout
            while time.monotonic() < limite:
                r = self._servidor.get_transaction(envio.hash)
                if r.status == GetTransactionStatus.SUCCESS:
                    return ResultadoRegistro(tx_hash=envio.hash, ledger=r.ledger)
                if r.status == GetTransactionStatus.FAILED:
                    # Puede ser una carrera con otro envío del mismo hash.
                    existente = self.obtener(hash_integridad)
                    if existente and existente.seudonimo == bytes(seudonimo):
                        return ResultadoRegistro(tx_hash=envio.hash, ledger=existente.ledger, ya_existia=True)
                    raise ErrorSoroban("tx_fallida", False, envio.hash)
                time.sleep(2)
            # Estado desconocido: puede entrar más tarde. Reintentar es seguro (idempotente por hash).
            raise ErrorSoroban("tx_sin_confirmar", True, envio.hash)
        except ErrorSoroban:
            raise
        except Exception as exc:  # red, RPC caído, respuesta inesperada
            raise ErrorSoroban(f"rpc_{type(exc).__name__}", True) from exc

    def _confirmar_existente(self, seudonimo: bytes, hash_integridad: bytes) -> ResultadoRegistro:
        existente = self.obtener(hash_integridad)
        if existente is None:
            raise ErrorSoroban("ya_registrado_sin_prueba", True)
        if existente.seudonimo != bytes(seudonimo):
            # Mismo hash con otro dueño: no debería ocurrir (nonce aleatorio). Es un error grave.
            raise ErrorSoroban("hash_de_otro_seudonimo", False)
        return ResultadoRegistro(tx_hash=None, ledger=existente.ledger, ya_existia=True)

    def obtener(self, hash_integridad: bytes) -> Optional[PruebaOnChain]:
        _exigir_32_bytes("hash_integridad", hash_integridad)
        from stellar_sdk import scval, xdr as sxdr

        try:
            sim = self._servidor.simulate_transaction(self._tx("obtener", [scval.to_bytes(bytes(hash_integridad))]))
        except Exception as exc:
            raise ErrorSoroban(f"rpc_{type(exc).__name__}", True) from exc
        if sim.error or not sim.results:
            raise ErrorSoroban("consulta_fallida", True)
        valor = sxdr.SCVal.from_xdr(sim.results[0].xdr)
        nativo = scval.to_native(valor)
        if nativo is None:
            return None
        return PruebaOnChain(seudonimo=bytes(nativo["seudonimo"]), ledger=int(nativo["ledger"]))


def crear_publicador() -> PublicadorLibroPersonal:
    cfg = obtener_config()
    if not cfg.soroban_contrato_libro_personal or not cfg.soroban_llave_servicio_archivo:
        return PublicadorNoConfigurado()
    from stellar_sdk import Keypair

    try:
        with open(cfg.soroban_llave_servicio_archivo, encoding="utf-8") as f:
            firmante = Keypair.from_secret(f.read().strip())
    except Exception:
        log.error("no se pudo cargar la llave de servicio (ruta configurada, contenido no mostrado)")
        return PublicadorNoConfigurado()
    return PublicadorSoroban(
        cfg.soroban_rpc_url, cfg.soroban_red, cfg.soroban_contrato_libro_personal, firmante, cfg.soroban_timeout_s
    )
