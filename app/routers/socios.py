"""
API de gobernanza: socios, credenciales, propuestas y votos.

- Admin (X-Api-Token = COLMENA_API_TOKEN): alta de socios y emisión de credenciales.
- Socio (X-Socio-Token = token de su credencial): proponer, votar y consultar.
"""
import json
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Propuesta, Socio
from ..seguridad import verificar_token
from ..services import gobernanza as servicio
from ..services.gobernanza import ErrorGobernanza

router = APIRouter(prefix="/api/v1", tags=["Gobernanza (socios)"])


# ----------------------------------------------------------------- autenticación

def _autenticar_admin(x_api_token: Optional[str] = Header(None)) -> None:
    verificar_token(x_api_token)


def socio_actual(
    x_socio_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> Socio:
    try:
        return servicio.autenticar_token(db, x_socio_token)
    except ErrorGobernanza as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detalle)


def _traducir(exc: ErrorGobernanza) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detalle)


# ----------------------------------------------------------------- esquemas

class SocioEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: str = Field(min_length=1, max_length=80)
    public_key: str = Field(min_length=56, max_length=56, pattern=r"^G[A-Z2-7]{55}$")


class SocioSalida(BaseModel):
    id: int
    nombre: str
    public_key: str
    activo: bool


class CredencialEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: str = Field(default="Token principal", min_length=1, max_length=80)


class CredencialSalida(BaseModel):
    id: int
    socio_id: int
    nombre: str
    # Se muestra UNA sola vez. No se puede recuperar después: solo queda su hash.
    token: str


class PropuestaEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["compra", "cambiar_modalidad", "agregar_socio"]
    modalidad: Literal["unanimidad", "mayoria_simple"]
    datos_publicos: dict[str, Any] = Field(default_factory=dict)


class VotoEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a_favor: bool


class PropuestaSalida(BaseModel):
    id: int
    tipo: str
    modalidad: str
    estado: str
    proponente_id: int
    socios_habilitados: int
    votos_requeridos: int
    a_favor: int
    en_contra: int
    pendientes: int
    mi_voto: Optional[bool] = None
    datos_publicos: dict[str, Any]
    creado_en: str


def _propuesta_salida(db: Session, p: Propuesta, socio: Socio) -> PropuestaSalida:
    r = servicio.recuento(db, p)
    return PropuestaSalida(
        id=p.id,
        tipo=p.tipo,
        modalidad=p.modalidad,
        estado=p.estado,
        proponente_id=p.proponente_id,
        socios_habilitados=p.socios_habilitados,
        votos_requeridos=p.votos_requeridos,
        a_favor=r.a_favor,
        en_contra=r.en_contra,
        pendientes=r.pendientes,
        mi_voto=servicio.voto_de(db, p.id, socio.id),
        datos_publicos=json.loads(p.datos_publicos),
        creado_en=p.creado_en,
    )


# ----------------------------------------------------------------- admin

@router.post(
    "/socios",
    response_model=SocioSalida,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_autenticar_admin)],
)
def crear_socio(datos: SocioEntrada, db: Session = Depends(get_db)):
    try:
        socio = servicio.crear_socio(db, datos.nombre, datos.public_key)
    except ErrorGobernanza as exc:
        raise _traducir(exc)
    return SocioSalida(id=socio.id, nombre=socio.nombre, public_key=socio.public_key, activo=socio.activo)


@router.post(
    "/socios/{socio_id}/credenciales",
    response_model=CredencialSalida,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_autenticar_admin)],
)
def emitir_credencial(socio_id: int, datos: Optional[CredencialEntrada] = None, db: Session = Depends(get_db)):
    nombre = datos.nombre if datos else "Token principal"
    try:
        credencial, token = servicio.emitir_credencial(db, socio_id, nombre)
    except ErrorGobernanza as exc:
        raise _traducir(exc)
    return CredencialSalida(id=credencial.id, socio_id=socio_id, nombre=credencial.nombre, token=token)


# ----------------------------------------------------------------- socios

@router.post("/propuestas", response_model=PropuestaSalida, status_code=status.HTTP_201_CREATED)
def crear_propuesta(datos: PropuestaEntrada, socio: Socio = Depends(socio_actual), db: Session = Depends(get_db)):
    try:
        propuesta = servicio.crear_propuesta(db, socio, datos.tipo, datos.modalidad, datos.datos_publicos)
    except ErrorGobernanza as exc:
        raise _traducir(exc)
    return _propuesta_salida(db, propuesta, socio)


@router.post("/propuestas/{propuesta_id}/votos", response_model=PropuestaSalida, status_code=status.HTTP_201_CREATED)
def votar(
    propuesta_id: int,
    datos: VotoEntrada,
    socio: Socio = Depends(socio_actual),
    db: Session = Depends(get_db),
):
    try:
        propuesta, _ = servicio.emitir_voto(db, propuesta_id, socio, datos.a_favor)
    except ErrorGobernanza as exc:
        raise _traducir(exc)
    return _propuesta_salida(db, propuesta, socio)


@router.get("/propuestas", response_model=list[PropuestaSalida])
def listar_propuestas(socio: Socio = Depends(socio_actual), db: Session = Depends(get_db)):
    return [_propuesta_salida(db, p, socio) for p in servicio.listar_propuestas(db)]


@router.get("/propuestas/{propuesta_id}", response_model=PropuestaSalida)
def detalle_propuesta(propuesta_id: int, socio: Socio = Depends(socio_actual), db: Session = Depends(get_db)):
    try:
        propuesta = servicio.obtener_propuesta(db, propuesta_id)
    except ErrorGobernanza as exc:
        raise _traducir(exc)
    return _propuesta_salida(db, propuesta, socio)
