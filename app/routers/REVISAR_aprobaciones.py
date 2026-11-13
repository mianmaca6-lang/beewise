from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Aprobacion, FirmaAprobacion, RegistroArchivo
from ..schemas import AprobacionCrearIn, AprobacionOut, FirmaIn
from ..seguridad import verificar_token
from ..services import REVISAR_stellar_aprobaciones as stellar  # REVISAR: reemplazado por contrato Tesoreria (fase 4)

router = APIRouter(prefix="/api/aprobaciones", tags=["Aprobaciones Stellar"])

ESTADOS_ABIERTOS = ("pendiente", "parcial", "aprobada")


def _token(x_api_token: Optional[str] = Header(None)) -> None:
    verificar_token(x_api_token)


def _config() -> stellar.StellarConfig:
    try:
        return stellar.cargar_config()
    except stellar.ConfigError as exc:
        raise HTTPException(status_code=503, detail=f"Configuración Stellar inválida: {exc}")


def _salida(a: Aprobacion) -> AprobacionOut:
    return AprobacionOut(
        id=a.id,
        registro_id=a.registro_id,
        estado=a.estado,
        umbral=a.umbral,
        firmas_validas=len(a.firmas),
        firmantes=[f.public_key for f in a.firmas],
        tx_hash=a.tx_hash,
        xdr_base=a.xdr_base,
        expira_en=a.expira_en,
        horizon_hash=a.horizon_hash,
        error_detalle=a.error_detalle,
        red=a.red,
    )


def _obtener(db: Session, aprobacion_id: int) -> Aprobacion:
    a = db.get(Aprobacion, aprobacion_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Aprobación no encontrada")
    return a


def _marcar_expirada_si_aplica(db: Session, a: Aprobacion) -> bool:
    if a.estado != "enviada" and stellar.expirada(a):
        if a.estado != "error":
            a.estado = "error"
            a.error_detalle = "expirada"
            db.commit()
            db.refresh(a)
        return True
    return False


@router.post("", response_model=AprobacionOut, status_code=201, dependencies=[Depends(_token)])
def crear_aprobacion(datos: AprobacionCrearIn, response: Response, db: Session = Depends(get_db)):
    registro = db.get(RegistroArchivo, datos.registro_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    existente = db.query(Aprobacion).filter(Aprobacion.registro_id == datos.registro_id).first()
    if existente is not None:
        response.status_code = 200
        return _salida(existente)

    abierta = db.query(Aprobacion).filter(Aprobacion.estado.in_(ESTADOS_ABIERTOS)).first()
    if abierta is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Ya hay una aprobación abierta (id {abierta.id}); comparten el número de secuencia de la cuenta",
        )

    cfg = _config()
    f_hash = stellar.hash_factura(registro, datos.monto)
    try:
        env = stellar.construir_tx(cfg, registro.id, f_hash)
    except stellar.StellarError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    expira = datetime.now(timezone.utc) + timedelta(hours=cfg.ttl_horas)
    a = Aprobacion(
        registro_id=registro.id,
        factura_hash=f_hash,
        monto=datos.monto,
        tx_hash=env.hash_hex(),
        xdr_base=env.to_xdr(),
        cuenta_origen=cfg.cuenta,
        red=cfg.red,
        estado="pendiente",
        umbral=cfg.umbral,
        expira_en=expira.isoformat(),
    )
    db.add(a)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Aprobación duplicada para esta factura")
    db.refresh(a)
    return _salida(a)


@router.post("/{aprobacion_id}/firmas", response_model=AprobacionOut, dependencies=[Depends(_token)])
def subir_firma(aprobacion_id: int, datos: FirmaIn, db: Session = Depends(get_db)):
    a = _obtener(db, aprobacion_id)
    if a.estado == "enviada":
        raise HTTPException(status_code=409, detail="La aprobación ya fue enviada")
    if _marcar_expirada_si_aplica(db, a):
        raise HTTPException(status_code=409, detail="La aprobación expiró")
    if a.estado == "error":
        raise HTTPException(status_code=409, detail=f"Aprobación en error: {a.error_detalle}")

    cfg = _config()
    try:
        public_key, firma_b64 = stellar.validar_firma(cfg, a, datos.xdr_firmado)
    except stellar.FirmaInvalida as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not any(f.public_key == public_key for f in a.firmas):
        db.add(FirmaAprobacion(aprobacion_id=a.id, public_key=public_key, firma_b64=firma_b64))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # carrera: el mismo socio ya firmó; es idempotente
        db.refresh(a)

    nuevo = stellar.calcular_estado(a, len(a.firmas))
    if nuevo != a.estado:
        a.estado = nuevo
        db.commit()
        db.refresh(a)
    return _salida(a)


@router.get("/{aprobacion_id}", response_model=AprobacionOut, dependencies=[Depends(_token)])
def obtener_aprobacion(aprobacion_id: int, db: Session = Depends(get_db)):
    a = _obtener(db, aprobacion_id)
    _marcar_expirada_si_aplica(db, a)
    return _salida(a)


@router.post("/{aprobacion_id}/enviar", response_model=AprobacionOut, dependencies=[Depends(_token)])
def enviar_aprobacion(aprobacion_id: int, db: Session = Depends(get_db)):
    a = _obtener(db, aprobacion_id)
    if a.estado == "enviada":
        return _salida(a)
    if _marcar_expirada_si_aplica(db, a):
        raise HTTPException(status_code=409, detail="La aprobación expiró")
    if a.estado != "aprobada":
        raise HTTPException(status_code=409, detail=f"La aprobación está '{a.estado}', se requiere 'aprobada'")

    cfg = _config()
    try:
        horizon_hash = stellar.enviar(cfg, a)
    except stellar.StellarError as exc:
        detalle = str(exc)
        if not exc.definitivo:
            # Fallo ambiguo: puede que Horizon sí haya recibido la tx
            try:
                if stellar.existe_en_horizon(cfg, a.tx_hash):
                    a.estado = "enviada"
                    a.horizon_hash = a.tx_hash
                    a.error_detalle = None
                    db.commit()
                    db.refresh(a)
                    return _salida(a)
            except stellar.StellarError as exc2:
                detalle = f"{detalle}; verificación fallida: {exc2}"
        a.estado = "error"
        a.error_detalle = detalle[:500]
        db.commit()
        raise HTTPException(status_code=502, detail=detalle)

    a.estado = "enviada"
    a.horizon_hash = horizon_hash
    a.error_detalle = None
    db.commit()
    db.refresh(a)
    return _salida(a)
