"""
Lógica de negocio del libro personal (independiente de HTTP y de Soroban).

Flujo:
  1. crear_registro(): normaliza → canónico + nonce → hash → cifra → guarda en SQLite
     (estado 'pendiente') en UNA transacción, con historial. Idempotente por
     (usuario, clave_idempotencia).
  2. publicar(): envía SOLO (seudónimo, hash) a Soroban mediante el publicador.
     Cada resultado queda registrado (estado + transacciones_soroban + historial):
       éxito / ya existía on-chain   → 'confirmado'
       error transitorio o ambiguo   → 'error_reintentable' (reintentar es seguro:
                                        el contrato rechaza hashes duplicados)
       error definitivo              → 'error'
     Si el proceso muere a mitad, el registro queda en 'enviado' (visible y reintentable).
  3. leer_registro(): descifra localmente y verifica que el hash coincide.
"""
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..logging_seguro import obtener_logger
from ..models import HistorialOperacion, RegistroPersonal, TransaccionSoroban, Usuario
from . import cifrado, identidad, integridad
from .soroban_libro import ErrorSoroban, PublicadorLibroPersonal

log = obtener_logger("registros_personales")

_NONCE_HUELLA = "0" * 64  # marcador fijo: la huella no debe depender del nonce aleatorio


class ConflictoIdempotencia(Exception):
    """La clave de idempotencia ya se usó con un contenido distinto."""


class ErrorIntegridad(Exception):
    """El contenido descifrado no corresponde al hash guardado."""


@dataclass(frozen=True)
class EntradaRegistro:
    canal: str
    id_externo: str
    tipo: str
    monto: Decimal
    moneda: str
    categoria: str
    concepto: str
    fecha: datetime


def aad_registro(usuario_id: int) -> bytes:
    return f"colmena/registro_personal/v1/usuario/{usuario_id}".encode()


def _historial(db: Session, registro_id: Optional[int], accion: str, resultado: str, detalle: Optional[str] = None):
    db.add(
        HistorialOperacion(
            entidad="registro_personal", entidad_id=registro_id, accion=accion, resultado=resultado,
            detalle=(detalle or None) and detalle[:200],
        )
    )


def obtener_o_crear_usuario(db: Session, canal: str, id_externo: str) -> Usuario:
    h = identidad.hmac_id_externo(canal, id_externo)
    usuario = db.query(Usuario).filter(Usuario.id_externo_hmac == h).first()
    if usuario:
        return usuario
    usuario = Usuario(canal=canal, id_externo_hmac=h, seudonimo=identidad.nuevo_seudonimo())
    db.add(usuario)
    try:
        db.commit()
    except IntegrityError:  # otra solicitud lo creó al mismo tiempo
        db.rollback()
        usuario = db.query(Usuario).filter(Usuario.id_externo_hmac == h).one()
    db.refresh(usuario)
    return usuario


def _canonico(usuario: Usuario, e: EntradaRegistro, nonce: str) -> dict:
    return integridad.construir_canonico(
        seudonimo=usuario.seudonimo, tipo=e.tipo, monto=e.monto, moneda=e.moneda,
        categoria=e.categoria, concepto=e.concepto, fecha=e.fecha, nonce=nonce,
    )


def crear_registro(db: Session, entrada: EntradaRegistro, clave_idempotencia: str) -> Tuple[RegistroPersonal, bool]:
    """Devuelve (registro, creado). creado=False si es una repetición idempotente."""
    clave = cifrado.cargar_clave()  # falla antes de tocar la BD si no hay clave
    usuario = obtener_o_crear_usuario(db, entrada.canal, entrada.id_externo)
    huella = identidad.huella_solicitud(integridad.serializar(_canonico(usuario, entrada, _NONCE_HUELLA)))

    def _repeticion() -> Optional[RegistroPersonal]:
        previo = (
            db.query(RegistroPersonal)
            .filter(RegistroPersonal.usuario_id == usuario.id, RegistroPersonal.clave_idempotencia == clave_idempotencia)
            .first()
        )
        if previo is not None and previo.huella_solicitud != huella:
            raise ConflictoIdempotencia()
        return previo

    previo = _repeticion()
    if previo is not None:
        return previo, False

    serializado = integridad.serializar(_canonico(usuario, entrada, integridad.nuevo_nonce()))
    registro = RegistroPersonal(
        usuario_id=usuario.id,
        clave_idempotencia=clave_idempotencia,
        huella_solicitud=huella,
        contenido_cifrado=cifrado.cifrar(serializado, aad_registro(usuario.id), clave),
        version_clave=clave.version,
        hash_integridad=integridad.calcular_hash(serializado).hex(),
        estado_sincronizacion="pendiente",
    )
    db.add(registro)
    try:
        db.flush()
        _historial(db, registro.id, "crear", "ok")
        db.commit()
    except IntegrityError:
        db.rollback()
        previo = _repeticion()
        if previo is not None:
            return previo, False
        raise
    db.refresh(registro)
    log.info("registro personal creado", extra={"datos": {"registro_id": registro.id, "estado": "pendiente"}})
    return registro, True


def publicar(db: Session, registro_id: int, publicador: PublicadorLibroPersonal) -> RegistroPersonal:
    registro = db.get(RegistroPersonal, registro_id)
    if registro is None:
        raise LookupError("registro no encontrado")
    if registro.estado_sincronizacion == "confirmado":
        return registro

    tx = TransaccionSoroban(contrato="libro_personal", operacion="registrar", estado="preparada")
    db.add(tx)
    db.flush()
    registro.transaccion_soroban_id = tx.id
    registro.estado_sincronizacion = "enviado"
    _historial(db, registro.id, "publicar", "iniciado", f"tx_local={tx.id}")
    db.commit()

    seudonimo = bytes.fromhex(registro.usuario.seudonimo)
    hash_bytes = bytes.fromhex(registro.hash_integridad)
    try:
        resultado = publicador.registrar(seudonimo, hash_bytes)
    except ErrorSoroban as exc:
        tx.estado = "desconocido" if (exc.reintentable and exc.tx_hash) else "fallida"
        tx.tx_hash = exc.tx_hash
        tx.codigo_error = exc.codigo
        registro.estado_sincronizacion = "error_reintentable" if exc.reintentable else "error"
        _historial(db, registro.id, "publicar", registro.estado_sincronizacion, exc.codigo)
        db.commit()
        log.warning(
            "fallo publicando en Soroban",
            extra={"datos": {"registro_id": registro.id, "codigo": exc.codigo, "reintentable": exc.reintentable}},
        )
        return registro
    except Exception as exc:  # nunca dejar el fallo en silencio
        tx.estado = "fallida"
        tx.codigo_error = f"interno_{type(exc).__name__}"[:60]
        registro.estado_sincronizacion = "error_reintentable"
        _historial(db, registro.id, "publicar", "error_reintentable", tx.codigo_error)
        db.commit()
        log.exception("error interno publicando", extra={"datos": {"registro_id": registro.id}})
        return registro

    tx.estado = "exito"
    tx.tx_hash = resultado.tx_hash
    tx.ledger = resultado.ledger
    registro.estado_sincronizacion = "confirmado"
    _historial(db, registro.id, "publicar", "confirmado", "ya_existia" if resultado.ya_existia else None)
    db.commit()
    log.info("registro confirmado en Soroban", extra={"datos": {"registro_id": registro.id, "ledger": resultado.ledger}})
    return registro


def leer_registro(db: Session, registro_id: int, clave: Optional[cifrado.ClaveCifrado] = None) -> dict:
    """Descifra y verifica integridad. Uso local; nunca registrar el resultado en logs."""
    registro = db.get(RegistroPersonal, registro_id)
    if registro is None:
        raise LookupError("registro no encontrado")
    clave = clave or cifrado.cargar_clave()
    serializado = cifrado.descifrar(registro.contenido_cifrado, aad_registro(registro.usuario_id), clave)
    if not integridad.verificar(serializado, registro.hash_integridad):
        raise ErrorIntegridad("el contenido no coincide con su hash de integridad")
    return json.loads(serializado.decode("utf-8"))
