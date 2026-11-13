"""
API de registros financieros para n8n (Telegram → n8n → FastAPI → Soroban).

- Autenticación: header X-Api-Token (COLMENA_API_TOKEN).
- Idempotencia: header Idempotency-Key obligatorio. Repetir la solicitud con la misma
  clave y el mismo contenido devuelve el registro existente (200) sin crear otro;
  con contenido distinto → 409.
- Ámbito 'personal': se guarda cifrado y a Soroban solo va (seudónimo, hash).
- Ámbito 'socio': todavía no implementado (contrato de socios en fase posterior) → 501,
  sin guardar nada.
- Las respuestas nunca incluyen monto, categoría ni concepto.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..logging_seguro import obtener_logger
from ..models import RegistroPersonal
from ..seguridad import verificar_token
from ..services import registros_personales as servicio
from ..services.cifrado import ErrorClave
from ..services.diagnostico import estado_privacidad
from ..services.identidad import ErrorSecreto
from ..services.integridad import RegistroInvalido
from ..services.soroban_libro import PublicadorLibroPersonal, crear_publicador

log = obtener_logger("registros")
router = APIRouter(prefix="/api/v1/registros", tags=["Registros financieros (n8n)"])

_PATRON_IDEMPOTENCIA = r"^[A-Za-z0-9_\-:.]{8,128}$"


def _autenticar(x_api_token: Optional[str] = Header(None)) -> None:
    verificar_token(x_api_token)


def obtener_publicador() -> PublicadorLibroPersonal:
    """Dependencia sustituible en pruebas."""
    return crear_publicador()


class UsuarioEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canal: Literal["telegram"]
    id_externo: str = Field(min_length=1, max_length=64)


class RegistroEntradaAPI(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ambito: Literal["personal", "socio"]
    usuario: UsuarioEntrada
    tipo: Literal["gasto", "ingreso", "ahorro"]
    monto: Decimal = Field(gt=0, le=Decimal("1000000000000"))
    moneda: str = Field(default="COP", pattern=r"^[A-Za-z]{3}$")
    categoria: str = Field(min_length=1, max_length=60)
    concepto: str = Field(min_length=1, max_length=280)
    fecha: datetime


class RegistroSalida(BaseModel):
    id: int
    estado_sincronizacion: str
    hash_integridad: str
    duplicado: bool = False
    tx_hash: Optional[str] = None
    ledger: Optional[int] = None


def _salida(r: RegistroPersonal, duplicado: bool = False) -> RegistroSalida:
    tx = r.transaccion_soroban
    return RegistroSalida(
        id=r.id,
        estado_sincronizacion=r.estado_sincronizacion,
        hash_integridad=r.hash_integridad,
        duplicado=duplicado,
        tx_hash=tx.tx_hash if tx else None,
        ledger=tx.ledger if tx else None,
    )


def _publicar_en_segundo_plano(registro_id: int, publicador: PublicadorLibroPersonal) -> None:
    db = SessionLocal()
    try:
        servicio.publicar(db, registro_id, publicador)
    finally:
        db.close()


@router.post("", response_model=RegistroSalida, status_code=status.HTTP_201_CREATED, dependencies=[Depends(_autenticar)])
def crear_registro(
    datos: RegistroEntradaAPI,
    response: Response,
    tareas: BackgroundTasks,
    idempotency_key: str = Header(..., alias="Idempotency-Key", pattern=_PATRON_IDEMPOTENCIA),
    db: Session = Depends(get_db),
    publicador: PublicadorLibroPersonal = Depends(obtener_publicador),
):
    if datos.ambito == "socio":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Los registros de socios se implementan en una fase posterior (libro de socios separado).",
        )
    entrada = servicio.EntradaRegistro(
        canal=datos.usuario.canal, id_externo=datos.usuario.id_externo, tipo=datos.tipo, monto=datos.monto,
        moneda=datos.moneda, categoria=datos.categoria, concepto=datos.concepto, fecha=datos.fecha,
    )
    try:
        registro, creado = servicio.crear_registro(db, entrada, idempotency_key)
    except RegistroInvalido as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except servicio.ConflictoIdempotencia:
        raise HTTPException(status_code=409, detail="Idempotency-Key ya usada con un contenido distinto")
    except (ErrorClave, ErrorSecreto):
        problemas = estado_privacidad()["problemas"] or ["causa no identificada"]
        log.error("configuración de privacidad incompleta", extra={"datos": {"problemas": problemas}})
        raise HTTPException(status_code=503, detail="Configuración de privacidad incompleta en el servidor")

    if not creado:
        response.status_code = status.HTTP_200_OK
        return _salida(registro, duplicado=True)

    tareas.add_task(_publicar_en_segundo_plano, registro.id, publicador)
    return _salida(registro)


@router.get("/{registro_id}", response_model=RegistroSalida, dependencies=[Depends(_autenticar)])
def consultar_estado(registro_id: int, db: Session = Depends(get_db)):
    registro = db.get(RegistroPersonal, registro_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    return _salida(registro)


@router.post("/{registro_id}/reintentar", response_model=RegistroSalida, dependencies=[Depends(_autenticar)])
def reintentar(
    registro_id: int,
    db: Session = Depends(get_db),
    publicador: PublicadorLibroPersonal = Depends(obtener_publicador),
):
    registro = db.get(RegistroPersonal, registro_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    if registro.estado_sincronizacion == "error":
        raise HTTPException(status_code=409, detail="Error definitivo: requiere revisión manual")
    return _salida(servicio.publicar(db, registro_id, publicador))
