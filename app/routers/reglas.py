from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Cliente, ReglaClasificacion
from ..services.foso_memoria import normalizar, registrar_nueva_regla

router = APIRouter(prefix="/api/reglas", tags=["reglas"])


class ReglaIn(BaseModel):
    palabra_clave: str = Field(..., description="Texto que identifica el gasto, ej. 'Gasolina'")
    cuenta_contable: str = Field(..., pattern=r"^\d{4,10}$", description="Código PUC, ej. '519595'")
    aplica_retencion: bool = False
    porcentaje_retencion: Optional[float] = Field(default=None, ge=0, le=100)
    cliente_id: Optional[int] = Field(default=None, ge=1)

    @field_validator("palabra_clave")
    @classmethod
    def _clave_no_vacia(cls, v: str) -> str:
        if not normalizar(v):
            raise ValueError("palabra_clave no puede estar vacía")
        return v.strip()

    @model_validator(mode="after")
    def _coherencia_retencion(self):
        if self.aplica_retencion and self.porcentaje_retencion is None:
            raise ValueError("porcentaje_retencion es obligatorio si aplica_retencion es true")
        if not self.aplica_retencion:
            self.porcentaje_retencion = 0.0
        return self


class ReglaOut(BaseModel):
    status: str  # "aprendido" | "actualizado"
    id: int
    palabra_clave: str
    cuenta_contable: str
    aplica_retencion: bool
    porcentaje_retencion: float
    cliente_id: Optional[int] = None


@router.post("/ensenar", response_model=ReglaOut, status_code=status.HTTP_201_CREATED)
def ensenar_regla(datos: ReglaIn, response: Response, db: Session = Depends(get_db)):
    """Enseña una regla al foso. 201 si es nueva, 200 si actualizó una existente."""
    if datos.cliente_id is not None and db.get(Cliente, datos.cliente_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe el cliente {datos.cliente_id}.",
        )

    # registrar_nueva_regla ya hace el commit
    resultado = registrar_nueva_regla(
        db,
        palabra_clave=datos.palabra_clave,
        cuenta_contable=datos.cuenta_contable,
        aplica_retencion=datos.aplica_retencion,
        porcentaje_retencion=datos.porcentaje_retencion or 0.0,
        cliente_id=datos.cliente_id,
    )
    if resultado["status"] == "actualizado":
        response.status_code = status.HTTP_200_OK

    regla = db.get(ReglaClasificacion, resultado["id"])
    return ReglaOut(
        status=resultado["status"],
        id=regla.id,
        palabra_clave=regla.palabra_clave,
        cuenta_contable=regla.cuenta_contable,
        aplica_retencion=regla.aplica_retencion,
        porcentaje_retencion=regla.porcentaje_retencion or 0.0,
        cliente_id=regla.cliente_id,
    )