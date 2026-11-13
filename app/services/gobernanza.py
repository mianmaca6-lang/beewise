"""
Socios, credenciales y propuestas de votación.

Reglas:
- El token de un socio se muestra una sola vez; en BD solo se guarda su SHA-256.
- Al crear una propuesta se fotografía el padrón activo (propuesta_socios_elegibles)
  y el quórum (votos_requeridos). Socios agregados después no votan en propuestas
  anteriores.
- Un socio vota una vez por propuesta (UNIQUE en votos) y solo si estaba en la foto.
- La propuesta se decide en cuanto se alcanza el quórum a favor (aprobada) o cuando ya
  no es posible alcanzarlo (rechazada).

Nunca se registran tokens, llaves ni contenido de datos_publicos en los logs.
"""
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from stellar_sdk.strkey import StrKey

from ..logging_seguro import obtener_logger
from ..models import CredencialSocio, Propuesta, PropuestaSocioElegible, Socio, Voto, _ahora_iso
from .votaciones import Modalidad, votos_requeridos

log = obtener_logger("gobernanza")

TAMANO_MAX_DATOS_PUBLICOS = 4000


class ErrorGobernanza(Exception):
    """Error de dominio; el router lo traduce a HTTP."""

    def __init__(self, status_code: int, detalle: str):
        super().__init__(detalle)
        self.status_code = status_code
        self.detalle = detalle


@dataclass(frozen=True)
class Recuento:
    a_favor: int
    en_contra: int
    habilitados: int
    requeridos: int

    @property
    def pendientes(self) -> int:
        return self.habilitados - self.a_favor - self.en_contra


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def crear_socio(db: Session, nombre: str, public_key: str) -> Socio:
    clave = public_key.strip()
    if not StrKey.is_valid_ed25519_public_key(clave):
        raise ErrorGobernanza(422, "public_key debe ser una clave pública Stellar válida (G...)")
    socio = Socio(nombre=nombre.strip(), public_key=clave, activo=True)
    db.add(socio)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ErrorGobernanza(409, "Ya existe un socio con esa public_key")
    db.refresh(socio)
    log.info("socio creado", extra={"datos": {"socio_id": socio.id}})
    return socio


def emitir_credencial(db: Session, socio_id: int, nombre: str) -> tuple[CredencialSocio, str]:
    socio = db.get(Socio, socio_id)
    if socio is None:
        raise ErrorGobernanza(404, "Socio no encontrado")
    if not socio.activo:
        raise ErrorGobernanza(409, "El socio está inactivo")
    token = secrets.token_urlsafe(32)
    credencial = CredencialSocio(socio_id=socio.id, token_hash=hash_token(token), nombre=nombre.strip())
    db.add(credencial)
    db.commit()
    db.refresh(credencial)
    log.info("credencial de socio emitida", extra={"datos": {"socio_id": socio.id, "credencial_id": credencial.id}})
    return credencial, token


def _vencida(expira_en: Optional[str]) -> bool:
    if not expira_en:
        return False
    return datetime.fromisoformat(expira_en) <= datetime.now(timezone.utc)


def autenticar_token(db: Session, token: Optional[str]) -> Socio:
    """Devuelve el socio dueño del token o lanza 401 (mensaje genérico, sin pistas)."""
    no_autorizado = ErrorGobernanza(401, "Credencial no válida")
    if not token:
        raise no_autorizado
    h = hash_token(token)
    credencial = db.query(CredencialSocio).filter(CredencialSocio.token_hash == h).first()
    # Comparación en tiempo constante sobre el hash; el índice ya acotó la fila.
    if credencial is None or not hmac.compare_digest(credencial.token_hash.encode(), h.encode()):
        raise no_autorizado
    socio = credencial.socio
    if (
        not credencial.activo
        or credencial.revocado_en is not None
        or _vencida(credencial.expira_en)
        or socio is None
        or not socio.activo
    ):
        raise no_autorizado
    credencial.ultimo_uso_en = _ahora_iso()
    db.commit()
    return socio


def padron_activo(db: Session) -> list[Socio]:
    return db.query(Socio).filter(Socio.activo.is_(True)).order_by(Socio.id).all()


def crear_propuesta(
    db: Session, proponente: Socio, tipo: str, modalidad: str, datos_publicos: dict[str, Any]
) -> Propuesta:
    serializado = json.dumps(datos_publicos, ensure_ascii=False, sort_keys=True)
    if len(serializado) > TAMANO_MAX_DATOS_PUBLICOS:
        raise ErrorGobernanza(422, f"datos_publicos supera {TAMANO_MAX_DATOS_PUBLICOS} caracteres")

    padron = padron_activo(db)
    if not padron:
        raise ErrorGobernanza(409, "No hay socios activos para votar")
    requeridos = votos_requeridos(len(padron), Modalidad(modalidad))

    try:
        propuesta = Propuesta(
            tipo=tipo,
            proponente_id=proponente.id,
            datos_publicos=serializado,
            modalidad=modalidad,
            socios_habilitados=len(padron),
            votos_requeridos=requeridos,
            estado="abierta",
        )
        db.add(propuesta)
        db.flush()
        for socio in padron:
            db.add(
                PropuestaSocioElegible(
                    propuesta_id=propuesta.id, socio_id=socio.id, public_key_snapshot=socio.public_key
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(propuesta)
    log.info(
        "propuesta creada",
        extra={"datos": {"propuesta_id": propuesta.id, "tipo": tipo, "habilitados": len(padron), "requeridos": requeridos}},
    )
    return propuesta


def es_elegible(db: Session, propuesta_id: int, socio_id: int) -> bool:
    return (
        db.query(PropuestaSocioElegible)
        .filter(PropuestaSocioElegible.propuesta_id == propuesta_id, PropuestaSocioElegible.socio_id == socio_id)
        .first()
        is not None
    )


def recuento(db: Session, propuesta: Propuesta) -> Recuento:
    a_favor = db.query(Voto).filter(Voto.propuesta_id == propuesta.id, Voto.a_favor.is_(True)).count()
    en_contra = db.query(Voto).filter(Voto.propuesta_id == propuesta.id, Voto.a_favor.is_(False)).count()
    return Recuento(
        a_favor=a_favor,
        en_contra=en_contra,
        habilitados=propuesta.socios_habilitados,
        requeridos=propuesta.votos_requeridos,
    )


def emitir_voto(db: Session, propuesta_id: int, socio: Socio, a_favor: bool) -> tuple[Propuesta, Recuento]:
    propuesta = db.get(Propuesta, propuesta_id)
    if propuesta is None:
        raise ErrorGobernanza(404, "Propuesta no encontrada")
    if propuesta.estado != "abierta":
        raise ErrorGobernanza(409, f"La propuesta ya está {propuesta.estado}")
    if not es_elegible(db, propuesta.id, socio.id):
        raise ErrorGobernanza(403, "No estabas habilitado para votar en esta propuesta")

    db.add(Voto(propuesta_id=propuesta.id, socio_id=socio.id, a_favor=a_favor))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ErrorGobernanza(409, "Ya registraste tu voto en esta propuesta")

    r = recuento(db, propuesta)
    if r.a_favor >= r.requeridos:
        propuesta.estado = "aprobada"
    elif r.en_contra > r.habilitados - r.requeridos:
        propuesta.estado = "rechazada"
    db.commit()
    db.refresh(propuesta)
    log.info(
        "voto registrado",
        extra={"datos": {"propuesta_id": propuesta.id, "socio_id": socio.id, "estado": propuesta.estado}},
    )
    return propuesta, recuento(db, propuesta)


def listar_propuestas(db: Session) -> list[Propuesta]:
    return db.query(Propuesta).order_by(Propuesta.id.desc()).all()


def obtener_propuesta(db: Session, propuesta_id: int) -> Propuesta:
    propuesta = db.get(Propuesta, propuesta_id)
    if propuesta is None:
        raise ErrorGobernanza(404, "Propuesta no encontrada")
    return propuesta


def voto_de(db: Session, propuesta_id: int, socio_id: int) -> Optional[bool]:
    voto = db.query(Voto).filter(Voto.propuesta_id == propuesta_id, Voto.socio_id == socio_id).first()
    return None if voto is None else voto.a_favor
