from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import RegistroArchivo
from ..schemas import FacturaOut, N8nRespuesta
from ..seguridad import verificar_token
from ..services.n8n_client import N8nError, disparar_workflow

router = APIRouter(prefix="/api/facturas", tags=["Facturas"])


def _obtener(db: Session, registro_id: int) -> RegistroArchivo:
    registro = db.get(RegistroArchivo, registro_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    return registro


@router.get("", response_model=List[FacturaOut])
def listar_facturas(estado: Optional[str] = None, db: Session = Depends(get_db)):
    consulta = db.query(RegistroArchivo)
    if estado:
        consulta = consulta.filter(RegistroArchivo.estado == estado)
    return consulta.order_by(RegistroArchivo.id).all()


@router.get("/{registro_id}", response_model=FacturaOut)
def obtener_factura(registro_id: int, db: Session = Depends(get_db)):
    return _obtener(db, registro_id)


@router.post("/{registro_id}/enviar-n8n", response_model=N8nRespuesta)
def enviar_a_n8n(
    registro_id: int,
    workflow: str = Query("clasificar-factura"),
    db: Session = Depends(get_db),
    x_api_token: Optional[str] = Header(None),
):
    verificar_token(x_api_token)
    registro = _obtener(db, registro_id)
    try:
        respuesta = disparar_workflow(
            workflow,
            {
                "id": registro.id,
                "nombre_archivo": registro.nombre_archivo,
                "texto_extraido": registro.texto_extraido,
            },
        )
    except N8nError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return N8nRespuesta(status="enviado", detalle=respuesta)
